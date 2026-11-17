"""One quiz room: its members, quiz, current question, answers and scores.

The server keeps the only authoritative copy of this state. Clients send
requests; only the room decides what happens and tells everyone with STATE.

Synchronization
---------------
Every room has ONE lock. Every method that reads or changes the room takes
it first, so only one thing happens in a room at a time, whichever thread
asked for it:

  * the reader threads of the host and of every player (their requests),
  * the room's timer thread (the question deadline and the live ticks),
  * the UDP thread (confirming a player's UDP address).

Rules that keep this correct:

  1. No socket is written while the lock is held. Replies are only queued
     (Connection.send); each connection's writer thread writes them later.
     Because they are queued while the lock is held, every client receives
     messages in the same order in which the room changed.
  2. Every handler first closes the question if its deadline has passed
     (_expire). A late answer is therefore rejected even if the timer thread
     has not woken up yet.
  3. A message is acted on only if it came from the member's CURRENT
     connection (member.conn is conn). Messages still queued from a
     connection that was replaced, removed or closed are dropped.
  4. Lock order is registry lock, then room lock. A room never takes the
     registry lock itself; NEXT_QUESTION and CLOSE_ROOM, which can release
     the host's ownership, are called with the registry lock already held.

Phases (report section 2.5): LOBBY -> OPEN <-> PAUSED -> RESULTS -> OPEN ...
-> FINISHED.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import unicodedata
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

from fatrat.common import log, new_id, new_token, now_ms
from fatrat.protocol import udp
from fatrat.protocol.catalogue import CATALOGUE, MAX_NAME, MAX_QUESTIONS
from fatrat.protocol.framing import MAX_FRAME, encode_json
from fatrat.protocol.messages import ProtocolError, validate_quiz
from fatrat.server.connection import error, result
from fatrat.server.projection import live_counts, remaining_ms, state_for
from fatrat.server.scoring import score

MAX_PLAYERS = 50          # ACTIVE players per room, host excluded (team decision)
START_GATE = 5            # connected ACTIVE players needed for START_QUIZ
UDP_CHALLENGE_MS = 10_000  # a UDP nonce must be used within 10 seconds
HOST_CACHE_SIZE = 32      # successful host requests remembered for replay
# Size budget (report section 2.2): the largest message is the host's STATE,
# which carries the whole quiz, the current question and one row per player
# who ever joined. It must always fit in one 1 MiB frame, so growth is refused
# before it could happen. These are generous upper estimates in bytes.
STATE_FIXED_BYTES = 24_000    # current question with texts, result, fixed fields
STATE_BYTES_PER_PLAYER = 400  # participant row + leaderboard row for one player

HOST_COMMANDS = frozenset(name for name, spec in CATALOGUE.items()
                          if spec.sender == "H" and name != "CREATE_ROOM")
NOOP = object()           # a handler returns this when the request changed nothing


def token_key(token: str) -> str:
    """SHA-256 of the decoded token. Tokens are looked up by this, never stored as an index."""
    return hashlib.sha256(base64.urlsafe_b64decode(token + "=")).hexdigest()


def clean_name(raw: str) -> Optional[str]:
    """Normalize a display name (Unicode NFC, trimmed). None if it is not 1..32 characters."""
    name = unicodedata.normalize("NFC", raw).strip()
    return name if 1 <= len(name) <= MAX_NAME else None


class Reject(Exception):
    """Raised by a handler to refuse a request with an ERROR and no state change."""

    def __init__(self, code: str, text: str, **extra):
        super().__init__(code)
        self.code, self.text, self.extra = code, text, extra


class Member:
    """The host or one player of a room.

    conn is the member's current TCP connection, or None while disconnected.
    A disconnected player keeps identity, score and slot and can come back
    with the reconnect token.
    """

    def __init__(self, room: "Room", role: str, username: str):
        self.room = room
        self.role = role                      # "HOST" or "PLAYER"
        self.username = username
        self.actor_id = new_id()              # stable identity inside this room
        self.session_id = new_id()            # changes on every reconnect
        self.token: Optional[str] = None      # reconnect token
        self.conn = None
        self.participation = "ACTIVE"         # ACTIVE, LEFT or REMOVED (players only)
        self.total_score = 0
        self.binding: Optional[dict] = None   # UDP registration, see bind_udp()


class Room:
    def __init__(self, pin: str, host_name: str, host_token: str, *,
                 on_finish: Callable[["Room"], None], udp_service):
        """Create an empty LOBBY.

        on_finish(room) is called once when the room becomes FINISHED, with
        the registry lock held, so the registry can release the host's
        ownership. udp_service remembers which UDP binding belongs to whom.
        """
        self.lock = threading.Lock()
        self.wake = threading.Event()         # set to wake the timer thread early
        self.room_id = new_id()
        self.pin = pin
        self.host_key = token_key(host_token)
        self._on_finish = on_finish
        self._udp = udp_service

        self.host = Member(self, "HOST", host_name)
        self.host.token = host_token
        self.players: Dict[str, Member] = {}          # actor_id -> player, never deleted
        self.tokens: Dict[str, Member] = {}           # token_key -> player
        self.names = set()                            # casefolded names, reserved until shutdown

        self.quiz: Optional[dict] = None              # {"title", "questions"}; editable list
        self.quiz_bytes = 0                           # encoded size of the quiz, for the size budget
        self.phase = "LOBBY"
        self.finish_reason: Optional[str] = None
        self.pause_reason: Optional[str] = None
        self.control_rev = 0                          # host actions and question closes
        self.state_rev = 0                            # every change sent in a STATE

        self.index: Optional[int] = None              # position of the current question
        self.current: Optional[dict] = None           # frozen copy of the current question
        self.attempt_id: Optional[str] = None
        self.deadline_ms = 0                          # valid in OPEN
        self.frozen_remaining = 0                     # valid in PAUSED
        self.answers: Dict[str, dict] = {}            # actor_id -> accepted answer, this attempt
        self.accepted: Dict[Tuple[str, str], dict] = {}   # (actor_id, request_id) -> answer data, forever
        self.last_result: Optional[dict] = None
        self.own_results: Dict[str, dict] = {}
        self.finished_count = 0
        self.final_leaderboard: Optional[list] = None
        self.host_cache: "OrderedDict[str, bytes]" = OrderedDict()   # request_id -> fingerprint

    # ------------------------------------------------------------------
    # Small helpers. The caller holds self.lock.
    # ------------------------------------------------------------------

    def active_players(self) -> List[Member]:
        return [p for p in self.players.values() if p.participation == "ACTIVE"]

    def _changed(self, control: bool = False) -> None:
        """Count a change. control=True for host actions and question closes."""
        self.state_rev += 1
        if control:
            self.control_rev += 1

    def broadcast(self) -> None:
        """Queue a separately filtered STATE for the host and every attached ACTIVE player."""
        for m in [self.host] + self.active_players():
            if m.conn is not None:
                m.conn.send(state_for(self, m))

    def _tell_host(self) -> None:
        if self.host.conn is not None:
            self.host.conn.send(state_for(self, self.host))

    def _identity(self, m: Member) -> dict:
        return {"room_id": self.room_id, "pin": self.pin, "session_id": m.session_id,
                "actor_id": m.actor_id, "role": m.role, "reconnect_token": m.token}

    def _drop_binding(self, m: Member) -> None:
        if m.binding:
            self._udp.forget(m.binding["binding_id"])
            m.binding = None

    def _detach(self, m: Member) -> None:
        """Cut the link between a member and its connection (both directions)."""
        if m.conn is not None:
            m.conn.member = None
        m.conn = None
        self._drop_binding(m)

    # ------------------------------------------------------------------
    # Question lifecycle. The caller holds self.lock.
    # ------------------------------------------------------------------

    def _open(self, index: int) -> None:
        """Start question `index` as a new attempt with its full time."""
        self.index = index
        self.current = self.quiz["questions"][index]
        self.attempt_id = new_id()
        self.answers = {}
        self.deadline_ms = now_ms() + self.current["time_limit_ms"]
        self.phase, self.pause_reason = "OPEN", None
        self._changed(control=True)
        self.wake.set()                               # the timer must learn the new deadline

    def _close(self, reason: str) -> None:
        """Score every accepted answer exactly once and move to RESULTS.

        Points are awarded here and nowhere else, so an answer can never be
        scored twice and correctness never leaks before the question closes.
        """
        q = self.current
        self.own_results = {}
        for p in self.players.values():               # includes players who left: their answer still counts
            answer = self.answers.get(p.actor_id)
            correct = answer is not None and answer["choice_id"] == q["correct_choice_id"]
            points = score(answer["remaining_ms"], q["time_limit_ms"]) if correct else 0
            p.total_score += points
            self.own_results[p.actor_id] = {"choice_id": answer["choice_id"] if answer else None,
                                            "correct": correct, "points": points,
                                            "total_score": p.total_score}
        self.last_result = {"question_id": q["question_id"], "attempt_id": self.attempt_id,
                            "correct_choice_id": q["correct_choice_id"], "reason": reason}
        self.phase, self.pause_reason = "RESULTS", None
        self._changed(control=True)
        log(f"room {self.pin}", f"question {self.index + 1} closed ({reason}), "
                                f"{len(self.answers)} answers -> RESULTS")

    def _expire(self) -> bool:
        """Close the question if its deadline has passed. True if it closed."""
        if self.phase == "OPEN" and now_ms() >= self.deadline_ms:
            self._close("TIMEOUT")
            return True
        return False

    def _all_answered(self) -> bool:
        """True when there is at least one eligible player and all of them have answered."""
        eligible = self.active_players()
        return bool(eligible) and all(p.actor_id in self.answers for p in eligible)

    def _finish(self, reason: str) -> None:
        """Move to FINISHED and freeze the history. Registry lock is held by the caller."""
        self.finished_count = len(self.quiz["questions"]) if self.quiz else 0
        if reason == "HOST_CLOSED":                   # an unclosed attempt is discarded, not scored
            self.index = self.current = self.attempt_id = None
            self.answers = {}
        self.phase, self.finish_reason, self.pause_reason = "FINISHED", reason, None
        self.frozen_remaining = 0
        self._changed(control=True)
        self.final_leaderboard = state_for(self, self.host)["data"]["leaderboard"]
        self._on_finish(self)
        self.wake.set()                               # lets the timer thread exit

    # ------------------------------------------------------------------
    # Requests from a connection that is not attached yet
    # ------------------------------------------------------------------

    def attach_host(self, conn, rid: str) -> None:
        """Attach the creating host. Called by the registry right after creating the room."""
        with self.lock:
            self.host.conn, conn.member = conn, self.host
            self._changed()
            conn.send(result(rid, self.room_id, self._identity(self.host)))
            conn.send(state_for(self, self.host))

    def join(self, conn, msg: dict) -> str:
        """JOIN_ROOM. Checks run in the report's order; the lock makes check-and-add one step.

        Without the lock two players could both see 49 players and both join.
        """
        data, rid = msg["data"], msg["request_id"]
        name = clean_name(data["username"])
        with self.lock:
            if self._expire():
                self.broadcast()
            if self.phase == "FINISHED":
                code, text = "ROOM_FINISHED", "this room has finished"
            elif name is None:
                code, text = "INVALID_USERNAME", f"names must be 1..{MAX_NAME} characters"
            elif name.casefold() in self.names:
                code, text = "NAME_TAKEN", "that name is already used in this room"
            elif len(self.active_players()) >= MAX_PLAYERS:      # disconnected ACTIVE players still count
                code, text = "ROOM_FULL", f"the room already has {MAX_PLAYERS} players"
            elif not self._fits(self.quiz_bytes, len(self.players) + 1):
                # Only after very many players joined and left: the history itself is too large.
                code, text = "SERVER_BUSY", "this room cannot take more participants; create a new room"
            else:
                m = Member(self, "PLAYER", name)
                m.token = new_token()
                m.conn, conn.member = conn, m
                self.players[m.actor_id] = m
                self.tokens[token_key(m.token)] = m
                self.names.add(name.casefold())
                self._changed()
                conn.send(result(rid, self.room_id, self._identity(m)))
                conn.send(state_for(self, m, recovery=True))     # joiner: RESULT, then STATE
                for other in [self.host] + self.active_players():
                    if other is not m and other.conn is not None:
                        other.conn.send(state_for(self, other))
                return f"{name!r} joined ({len(self.active_players())}/{MAX_PLAYERS})"
            conn.send(error(code, text, rid))
            return code

    def resume(self, conn, msg: dict) -> str:
        """RESUME_SESSION: attach a new connection to an existing identity."""
        rid, key = msg["request_id"], token_key(msg["data"]["reconnect_token"])
        with self.lock:
            if self._expire():
                self.broadcast()
            if hmac.compare_digest(key, self.host_key):
                m = self.host
            else:
                m = self.tokens.get(key)
            if m is None:
                conn.send(error("INVALID_TOKEN", "unknown room or token", rid))
                return "INVALID_TOKEN"
            if m.participation != "ACTIVE":
                reason = "HOST_REMOVED" if m.participation == "REMOVED" else "LEFT_ROOM"
                conn.send(error("SESSION_REVOKED", "this identity can no longer rejoin", rid, reason=reason))
                return f"SESSION_REVOKED ({reason})"
            old = m.conn
            if old is not None:                       # still attached somewhere else: replace it
                if m.role == "HOST" and self.phase == "OPEN":
                    self._pause("HOST_DISCONNECTED")  # a fast reconnect must not skip the explicit resume
                old.send({"v": 1, "type": "SESSION_REPLACED", "room_id": self.room_id,
                          "data": {"reason": "RECONNECTED_ELSEWHERE"}})
                old.close()
                self._detach(m)
            m.session_id = new_id()
            m.conn, conn.member = conn, m
            self._changed()
            conn.send(result(rid, self.room_id, self._identity(m)))
            conn.send(state_for(self, m, recovery=True))
            if m.role == "HOST":                      # host_connected changed: everyone is told
                for other in [self.host] + self.active_players():
                    if other is not m and other.conn is not None:
                        other.conn.send(state_for(self, other))
            else:
                self._tell_host()
            return f"{m.role.lower()} {m.username!r} resumed"

    # ------------------------------------------------------------------
    # Requests from an attached connection
    # ------------------------------------------------------------------

    def handle(self, conn, msg: dict) -> str:
        """Process one request. Returns a short text for the server log."""
        with self.lock:
            m = conn.member
            if m is None or m.conn is not conn:
                return "dropped (old connection)"
            return self._handle(m, msg)

    def _handle(self, m: Member, msg: dict) -> str:
        conn, mtype, rid, data = m.conn, msg["type"], msg["request_id"], msg["data"]
        if self._expire():                            # rule 2: the deadline is applied first
            self.broadcast()
        try:
            if mtype == "GET_STATE":
                conn.send(result(rid, self.room_id, {}))
                conn.send(state_for(self, m, recovery=True))
                return "RESULT + STATE"
            if mtype in ("SUBMIT_ANSWER", "LEAVE_ROOM"):
                if m.role != "PLAYER":
                    raise Reject("NOT_PLAYER", f"{mtype} is a player request")
                return self._submit(m, rid, data) if mtype == "SUBMIT_ANSWER" else self._leave(m, rid)
            if m.role != "HOST":
                raise Reject("NOT_HOST", f"{mtype} is a host command")
            return self._host_command(m, mtype, rid, data)
        except Reject as no:
            conn.send(error(no.code, no.text, rid, self.room_id, **no.extra))
            if no.code in ("STALE_STATE", "STALE_ATTEMPT"):
                conn.send(state_for(self, m))         # the client must look at fresh state
            return no.code

    def _host_command(self, m: Member, mtype: str, rid: str, data: dict) -> str:
        fingerprint = encode_json([mtype, data])
        if rid in self.host_cache:                    # a repeated request must not run twice
            if self.host_cache[rid] != fingerprint:
                raise Reject("REQUEST_ID_REUSED", "request_id reused with different content")
            m.conn.send(result(rid, self.room_id, {}))
            m.conn.send(state_for(self, m))
            return "replay -> original RESULT + STATE"
        if data["expected_control_rev"] != self.control_rev:
            # The host decided on an old picture of the room (for example the
            # question closed meanwhile). Refuse and send the current picture.
            raise Reject("STALE_STATE", "the room changed; check the new STATE",
                         current_control_rev=self.control_rev)
        handler = getattr(self, "_do_" + mtype.lower(), None)
        if handler is None:
            raise Reject("INVALID_STATE", f"{mtype} is not available in this version of the server")
        note = handler(data)
        m.conn.send(result(rid, self.room_id, {}))    # the requester gets RESULT before STATE
        if note is NOOP:
            return "no change"
        self.host_cache[rid] = fingerprint
        if len(self.host_cache) > HOST_CACHE_SIZE:
            self.host_cache.popitem(last=False)
        self.broadcast()
        return note

    # -- host commands: each returns a log note, NOOP, or raises Reject ----

    def _do_put_quiz(self, data: dict) -> str:
        if self.phase != "LOBBY":
            raise Reject("INVALID_STATE", "a quiz can be loaded only in the lobby")
        self._set_quiz(data["title"], data["questions"])
        return f"quiz loaded ({len(data['questions'])} questions)"

    def _do_start_quiz(self, data: dict) -> str:
        if self.phase != "LOBBY":
            raise Reject("INVALID_STATE", "the quiz has already started")
        if self.quiz is None:
            raise Reject("INVALID_STATE", "load a quiz first")
        connected = [p for p in self.active_players() if p.conn is not None]
        if len(connected) < START_GATE:
            raise Reject("NOT_ENOUGH_PLAYERS", f"{len(connected)} connected players; {START_GATE} needed")
        self._open(0)
        return f"question 1 OPEN for {len(self.active_players())} players"

    def _pause(self, reason: str) -> None:
        """OPEN -> PAUSED: remember the time left so the pause costs players nothing."""
        self.frozen_remaining = max(0, self.deadline_ms - now_ms())
        self.phase, self.pause_reason = "PAUSED", reason
        self._changed(control=True)

    def _do_pause_quiz(self, data: dict) -> str:
        if self.phase != "OPEN":
            raise Reject("INVALID_STATE", "no question is open")
        self._pause("MANUAL")
        return f"PAUSED with {self.frozen_remaining} ms left"

    def _do_resume_quiz(self, data: dict) -> str:
        if self.phase != "PAUSED" or self.pause_reason == "EDITING":
            raise Reject("INVALID_STATE", "the quiz is not paused")
        if self._all_answered():                      # e.g. the last unanswered player left during the pause
            self._close("ALL_ANSWERED")
            return "resumed -> RESULTS (everyone had answered)"
        self.deadline_ms = now_ms() + self.frozen_remaining
        self.phase, self.pause_reason = "OPEN", None
        self._changed(control=True)
        self.wake.set()
        return f"OPEN again with {self.frozen_remaining} ms left"

    def _do_close_question(self, data: dict) -> str:
        if self.phase not in ("OPEN", "PAUSED") or self.pause_reason == "EDITING":
            raise Reject("INVALID_STATE", "no question can be closed now")
        self._close("HOST_CLOSED")
        return "closed by host"

    def _do_next_question(self, data: dict) -> str:
        if self.phase != "RESULTS":
            raise Reject("INVALID_STATE", "the current question has not closed")
        if self.index + 1 < len(self.quiz["questions"]):
            self._open(self.index + 1)
            return f"question {self.index + 1} OPEN"
        self._finish("COMPLETED")
        return "FINISHED (COMPLETED)"

    def _do_close_room(self, data: dict):
        if self.phase == "FINISHED":
            return NOOP
        self._finish("HOST_CLOSED")
        return "FINISHED (HOST_CLOSED)"

    # -- editing (report section 2.5) ---------------------------------------
    #
    # Two kinds of edit:
    #   * the CURRENT question: EDIT_BEGIN pauses it, EDIT_COMMIT replaces it
    #     and restarts it as a new attempt (old answers are discarded, nobody
    #     is scored for them), EDIT_CANCEL keeps everything as it was;
    #   * FUTURE questions: edit, add, delete, reorder. Questions already
    #     played and the current one are locked, so history never changes.
    # No question dict is ever modified in place: the list gets a new dict.
    # The running attempt and old results keep pointing at their own copy.

    def _set_quiz(self, title: str, questions: list) -> None:
        """Validate a whole candidate quiz and commit it, or raise Reject with nothing changed."""
        try:
            validate_quiz(title, questions)
        except ProtocolError as exc:
            raise Reject("INVALID_QUIZ", exc.detail)
        size = len(encode_json({"title": title, "questions": questions}))
        if not self._fits(size, len(self.players)):
            raise Reject("INVALID_QUIZ", "the quiz is too large for this room")
        self.quiz, self.quiz_bytes = {"title": title, "questions": questions}, size
        self._changed(control=True)

    def _fits(self, quiz_bytes: int, player_count: int) -> bool:
        """True if the largest STATE would still fit in one frame."""
        return quiz_bytes + STATE_FIXED_BYTES + STATE_BYTES_PER_PLAYER * player_count <= MAX_FRAME

    def _editable_from(self) -> int:
        """Position of the first question that may still be changed."""
        if self.quiz is None:
            raise Reject("INVALID_STATE", "no quiz is loaded")
        return 0 if self.phase in ("LOBBY", "FINISHED") else self.index + 1

    def _position(self, question_id: str, start: int) -> int:
        """Position of an editable question. Unknown -> INVALID_QUIZ, locked -> QUESTION_LOCKED."""
        ids = [q["question_id"] for q in self.quiz["questions"]]
        if question_id not in ids:
            raise Reject("INVALID_QUIZ", "no such question in the quiz")
        position = ids.index(question_id)
        if position < start:
            if position == self.index and self.phase in ("OPEN", "PAUSED"):
                raise Reject("INVALID_STATE", "use EDIT_BEGIN to edit the current question")
            raise Reject("QUESTION_LOCKED", "that question has already been played")
        return position

    def _do_edit_begin(self, data: dict) -> str:
        if self.phase not in ("OPEN", "PAUSED") or self.pause_reason == "EDITING":
            raise Reject("INVALID_STATE", "there is no question to start editing")
        if data["question_id"] != self.current["question_id"]:
            raise Reject("STALE_ATTEMPT", "that is not the current question")
        if self.phase == "OPEN":
            self._pause("EDITING")                    # freezes the clock; answers are kept for now
        else:
            self.pause_reason = "EDITING"
            self._changed(control=True)
        return "editing the current question (paused)"

    def _do_edit_commit(self, data: dict) -> str:
        if self.pause_reason != "EDITING":
            raise Reject("INVALID_STATE", "no edit is in progress")
        new = data["question"]
        if new["question_id"] != self.current["question_id"]:
            raise Reject("STALE_ATTEMPT", "that is not the current question")
        questions = list(self.quiz["questions"])
        questions[self.index] = new
        self._set_quiz(self.quiz["title"], questions)
        # A changed question is a new attempt: the old answers were given to
        # different content, so they are dropped and nobody is scored for them.
        self.current, self.attempt_id, self.answers = new, new_id(), {}
        self.frozen_remaining = new["time_limit_ms"]
        self.pause_reason = "MANUAL"                  # stays paused until the host resumes
        return "current question replaced; new attempt, full time, still paused"

    def _do_edit_cancel(self, data: dict) -> str:
        if self.pause_reason != "EDITING":
            raise Reject("INVALID_STATE", "no edit is in progress")
        self.pause_reason = "MANUAL"
        self._changed(control=True)
        return "edit cancelled; question, answers and time unchanged, still paused"

    def _do_edit_question(self, data: dict):
        start = self._editable_from()
        new = data["question"]
        position = self._position(new["question_id"], start)
        if self.quiz["questions"][position] == new:
            return NOOP
        questions = list(self.quiz["questions"])
        questions[position] = new
        self._set_quiz(self.quiz["title"], questions)
        return f"question {position + 1} replaced"

    def _do_add_question(self, data: dict) -> str:
        start = self._editable_from()
        new, before = data["question"], data["before_question_id"]
        questions = list(self.quiz["questions"])
        if new["question_id"] in [q["question_id"] for q in questions]:
            raise Reject("INVALID_QUIZ", "a question with that ID already exists")
        if len(questions) >= MAX_QUESTIONS:
            raise Reject("QUESTION_LIMIT", f"a quiz has at most {MAX_QUESTIONS} questions")
        position = len(questions) if before is None else self._position(before, start)
        questions.insert(position, new)
        self._set_quiz(self.quiz["title"], questions)
        return f"question added at position {position + 1}"

    def _do_delete_question(self, data: dict) -> str:
        start = self._editable_from()
        position = self._position(data["question_id"], start)
        if len(self.quiz["questions"]) == 1:
            raise Reject("QUESTION_LIMIT", "a quiz needs at least one question")
        questions = list(self.quiz["questions"])
        del questions[position]
        self._set_quiz(self.quiz["title"], questions)
        return f"question {position + 1} deleted"

    def _do_reorder_questions(self, data: dict):
        start = self._editable_from()
        questions = self.quiz["questions"]
        current_order = [q["question_id"] for q in questions[start:]]
        wanted = data["question_ids"]
        if sorted(wanted) != sorted(current_order) or len(set(wanted)) != len(wanted):
            raise Reject("INVALID_ORDER", "list every question that can still be moved exactly once")
        if wanted == current_order:
            return NOOP
        by_id = {q["question_id"]: q for q in questions}
        self._set_quiz(self.quiz["title"], questions[:start] + [by_id[i] for i in wanted])
        return "future questions reordered"

    def _do_remove_player(self, data: dict):
        target = self.players.get(data["player_id"])
        if target is None:
            raise Reject("UNKNOWN_PLAYER", "no such player in this room")
        if target.participation != "ACTIVE":
            return NOOP                               # already gone: the first reason stays
        target.participation = "REMOVED"              # the token is now refused by resume()
        if target.conn is not None:
            target.conn.send({"v": 1, "type": "REMOVED", "room_id": self.room_id,
                              "data": {"reason": "HOST_REMOVED",
                                       "message": "You were removed from this quiz."}})
            target.conn.close()                       # after the notice has been written
        self._detach(target)
        self._changed(control=True)
        if self.phase == "OPEN" and self._all_answered():
            self._close("ALL_ANSWERED")
        return f"removed {target.username!r}"

    # -- player requests --------------------------------------------------

    def _submit(self, m: Member, rid: str, data: dict) -> str:
        """SUBMIT_ANSWER, in the validation order of report section 2.3."""
        earlier = self.accepted.get((m.actor_id, rid))
        if earlier is not None:                       # the same request again: same answer back
            if earlier != data:
                raise Reject("REQUEST_ID_REUSED", "request_id reused with different content")
            m.conn.send(result(rid, self.room_id, {"accepted": True, **data}))
            if data["attempt_id"] != self.attempt_id:
                m.conn.send(state_for(self, m))
            return "replay -> original RESULT"
        q = self.current
        if q is None:
            raise Reject("INVALID_STATE", "no question has started")
        if data["question_id"] != q["question_id"] or data["attempt_id"] != self.attempt_id:
            raise Reject("STALE_ATTEMPT", "that question attempt is not current")
        if self.phase in ("RESULTS", "FINISHED"):
            raise Reject("ANSWER_LATE", "the question has closed")
        if self.phase == "PAUSED":
            raise Reject("QUESTION_PAUSED", "the question is paused")
        if data["choice_id"] not in [c["choice_id"] for c in q["choices"]]:
            raise Reject("INVALID_CHOICE", "not a choice of this question")
        answer = self.answers.get(m.actor_id)
        if answer is not None:
            raise Reject("ALREADY_ANSWERED", "one answer per question", question_id=q["question_id"],
                         attempt_id=self.attempt_id, choice_id=answer["choice_id"])
        # The acceptance time is the server's clock, read here under the lock.
        # The client sends no time, so it cannot claim to have answered earlier.
        remaining = self.deadline_ms - now_ms()
        if remaining <= 0:                            # the deadline passed this very millisecond
            self._close("TIMEOUT")
            self.broadcast()
            raise Reject("ANSWER_LATE", "the question has closed")
        self.answers[m.actor_id] = {"choice_id": data["choice_id"], "remaining_ms": remaining}
        self.accepted[(m.actor_id, rid)] = dict(data)
        m.conn.send(result(rid, self.room_id, {"accepted": True, **data}))   # no correctness yet
        if self._all_answered():
            self._close("ALL_ANSWERED")
            self.broadcast()
        return f"accepted ({remaining} ms left)"

    def _leave(self, m: Member, rid: str) -> str:
        """LEAVE_ROOM: permanent. The score stays on the leaderboard; the slot is freed."""
        m.participation = "LEFT"                      # the token is now refused by resume()
        conn = m.conn
        conn.send(result(rid, self.room_id, {"left": True}))
        conn.close()
        self._detach(m)
        self._changed()
        if self.phase == "OPEN" and self._all_answered():
            self._close("ALL_ANSWERED")
        self.broadcast()
        return f"LEFT ({len(self.active_players())}/{MAX_PLAYERS} active)"

    # ------------------------------------------------------------------
    # Connection loss
    # ------------------------------------------------------------------

    def disconnected(self, conn) -> None:
        """The TCP connection ended without LEAVE_ROOM.

        A player stays ACTIVE and keeps its slot, score and right to answer.
        If the host is lost while a question is open, the question is paused
        so the players are not left with a running clock and nobody to
        continue; the host resumes it explicitly after reconnecting.
        """
        with self.lock:
            m = conn.member
            if m is None or m.conn is not conn:       # already replaced, removed or left
                return
            self._detach(m)
            self._changed()
            if m.role == "HOST":
                if not self._expire() and self.phase == "OPEN":   # an expired question closes instead
                    self._pause("HOST_DISCONNECTED")
                self.broadcast()
            else:
                self._tell_host()                     # only the host shows connection status
        log(f"room {self.pin}", f"{m.role.lower()} {m.username!r} disconnected (identity kept)")

    # ------------------------------------------------------------------
    # UDP registration (report section 2.6)
    # ------------------------------------------------------------------

    def bind_udp(self, conn, rid: str, server_udp_port: int) -> str:
        """REQUEST_UDP_BIND: give the client a one-time nonce to send over UDP."""
        with self.lock:
            m = conn.member
            if m is None or m.conn is not conn:
                return "dropped (old connection)"
            self._drop_binding(m)                     # a new bind replaces the old one
            m.binding = {"binding_id": new_id(), "nonce": secrets.token_hex(16),
                         "expires_ms": now_ms() + UDP_CHALLENGE_MS,
                         "udp_addr": None, "confirmed": False, "seq": 0}
            self._udp.register(m.binding["binding_id"], m)
            conn.send(result(rid, self.room_id, {
                "binding_id": m.binding["binding_id"], "udp_nonce": m.binding["nonce"],
                "server_udp_port": server_udp_port}))
            return "RESULT (binding + nonce)"

    def confirm_udp(self, m: Member, message: dict, addr) -> Optional[str]:
        """UDP_HELLO arrived for this member. Returns None if accepted, else why it was dropped.

        UDP has no connection, so anyone could send a datagram claiming to be
        a player. The nonce was given over that player's TCP connection, and
        the datagram must come from the same IP address as that connection.
        """
        with self.lock:
            b = m.binding
            if b is None or b["binding_id"] != message["binding_id"] or m.conn is None:
                return "old binding"
            if message["session_id"] != m.session_id:
                return "wrong session"
            if not hmac.compare_digest(message["data"]["nonce"], b["nonce"]):
                return "wrong nonce"
            if not b["confirmed"] and now_ms() > b["expires_ms"]:
                return "challenge expired"
            if addr[0] != m.conn.peer[0]:
                return f"source IP {addr[0]} is not the TCP peer {m.conn.peer[0]}"
            if b["udp_addr"] not in (None, addr):
                return "a different port needs a fresh REQUEST_UDP_BIND"
            b["udp_addr"], b["confirmed"] = addr, True
            m.conn.send({"v": 1, "type": "UDP_BOUND", "room_id": self.room_id,
                         "data": {"binding_id": b["binding_id"]}})
            return None

    # ------------------------------------------------------------------
    # Timer thread entry point
    # ------------------------------------------------------------------

    def tick(self, interval: float) -> Optional[Tuple[list, float]]:
        """One step of the room timer.

        Closes the question if its time is up and prepares one LIVE_STATUS
        datagram per registered client. Returns (datagrams, seconds to wait),
        or None when the room has finished and the timer thread should stop.
        The datagrams are returned, not sent, so no socket is used under the lock.
        """
        sends = []
        with self.lock:
            self.wake.clear()
            if self._expire():
                self.broadcast()
            if self.phase == "FINISHED":
                return None
            wait = interval
            if self.phase in ("OPEN", "PAUSED"):
                counts, left = live_counts(self), remaining_ms(self)
                if self.phase == "OPEN":
                    wait = min(wait, left / 1000)     # wake exactly at the deadline
                for m in [self.host] + self.active_players():
                    b = m.binding
                    if m.conn is None or not b or not b["confirmed"]:
                        continue
                    b["seq"] += 1                     # strictly increasing per binding
                    sends.append((udp.encode_datagram({
                        "v": 1, "type": "LIVE_STATUS", "room_id": self.room_id,
                        "session_id": m.session_id, "binding_id": b["binding_id"],
                        "question_id": self.current["question_id"], "attempt_id": self.attempt_id,
                        "control_rev": self.control_rev, "seq": b["seq"],
                        "data": {"remaining_ms": left, **counts}}), b["udp_addr"]))
        return sends, max(0.005, wait)
