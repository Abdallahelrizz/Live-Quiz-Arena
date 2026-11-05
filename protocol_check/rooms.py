"""Room engine of the protocol check server: real rooms with a host and players.

Used to test the parts of the protocol that need real rooms: the 50-player
maximum, the 5-player start gate, one unfinished room per host, and several
rooms running at the same time with their own players, timers and scores.

Supported, following report section 2: CREATE_ROOM (one unfinished room per
saved host_token, creation retry ledger), JOIN_ROOM (INVALID_PIN,
ROOM_FINISHED, INVALID_USERNAME, NAME_TAKEN, ROOM_FULL), PUT_QUIZ, START_QUIZ,
SUBMIT_ANSWER, CLOSE_QUESTION, NEXT_QUESTION, LEAVE_ROOM, GET_STATE, UDP
binding and LIVE_STATUS, scoring, grouped leaderboards and disconnects.
Pause, editing, removal, room closing and reconnect are not implemented
here and get INVALID_STATE "not supported".

Synchronization: one registry lock protects the PIN table, host ownership
and the creation ledger; each room has its own lock protecting everything in
that room. Lock order is always registry -> room, never the reverse. All
replies are queued (never written) while a lock is held.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import unicodedata
from typing import Dict, Optional

from fatrat.protocol import udp
from fatrat.protocol.framing import encode_json
from fatrat.protocol.messages import ProtocolError, is_pin, validate_quiz
from protocol_check.common import log, new_id, now_ms, score

MAX_PLAYERS = 50       # ACTIVE players per room, host excluded (team decision)
START_GATE = 5         # connected ACTIVE players needed to start
NOT_SUPPORTED = "not supported by the protocol check server (it is not the game server)"


def _error(code, text, reply_to, room_id=None, **extra):
    message = {"v": 1, "type": "ERROR", "reply_to": reply_to,
               "data": {"code": code, "message": text[:200], "retryable": False, **extra}}
    if room_id:
        message["room_id"] = room_id
    return message


def _result(reply_to, room_id, data):
    return {"v": 1, "type": "RESULT", "reply_to": reply_to, "room_id": room_id, "data": data}


class Member:
    """The host or a player of a room. Its lock is the room's lock."""

    def __init__(self, room: "Room", conn, role: str, username: str, token: str):
        self.room = room
        self.conn = conn                     # None while disconnected
        self.role = role
        self.username = username
        self.token = token
        self.actor_id = new_id()
        self.session_id = new_id()
        self.participation = "ACTIVE"
        self.total_score = 0
        self.binding: Optional[dict] = None

    @property
    def lock(self):
        return self.room.lock


class Room:
    def __init__(self, manager: "RoomManager", pin: str, host_conn, host_name: str, host_token: str, host_key: str):
        self.manager = manager
        self.lock = threading.Lock()
        self.room_id = new_id()
        self.pin = pin
        self.host_key = host_key
        self.host = Member(self, host_conn, "HOST", host_name, host_token)
        self.players: Dict[str, Member] = {}
        self.names = set()                   # casefolded player names, reserved until shutdown
        self.quiz: Optional[dict] = None
        self.phase = "LOBBY"
        self.finish_reason = None
        self.pause_reason = None
        self.control_rev = 0
        self.state_rev = 0
        self.index: Optional[int] = None
        self.attempt_id: Optional[str] = None
        self.deadline_ms = 0
        self.frozen_remaining = 0
        self.answers: Dict[str, dict] = {}   # actor_id -> accepted answer for the current attempt
        self.last_result: Optional[dict] = None
        self.own_results: Dict[str, dict] = {}

    # -- helpers (caller holds self.lock) ---------------------------------

    def active_players(self):
        return [p for p in self.players.values() if p.participation == "ACTIVE"]

    def question(self) -> Optional[dict]:
        return self.quiz["questions"][self.index] if self.quiz and self.index is not None else None

    def remaining(self) -> int:
        if self.phase == "OPEN":
            return max(0, self.deadline_ms - now_ms())
        return self.frozen_remaining if self.phase == "PAUSED" else 0

    def leaderboard(self):
        everyone = [(p.total_score, p.username, p.actor_id, p.participation) for p in self.players.values()]
        rows = []
        for points in sorted({e[0] for e in everyone}, reverse=True):     # dense ranks, ties grouped
            tied = sorted((e for e in everyone if e[0] == points), key=lambda e: e[1].casefold())
            rows.append({"rank": len(rows) + 1, "score": points, "players": [
                {"player_id": pid, "username": name, "participation": part} for _, name, pid, part in tied]})
        return rows

    def state_for(self, m: Member, recovery: bool = False) -> dict:
        host = m.role == "HOST"
        q = self.question()
        current = None
        if q is not None and not (self.phase == "FINISHED" and self.finish_reason == "HOST_CLOSED"):
            current = {"question_id": q["question_id"], "attempt_id": self.attempt_id,
                       "time_limit_ms": q["time_limit_ms"],
                       "choices": [{"choice_id": c["choice_id"], "symbol_index": i}
                                   for i, c in enumerate(q["choices"])]}
            if host:
                current["text"] = q["text"]
                for choice, full in zip(current["choices"], q["choices"]):
                    choice["text"] = full["text"]
        last = None
        if self.last_result:
            own = None
            if not host:
                own = self.own_results.get(m.actor_id) or {
                    "choice_id": None, "correct": False, "points": 0, "total_score": m.total_score}
            last = {**self.last_result, "own_result": own}
        live = None
        if recovery and self.phase in ("OPEN", "PAUSED"):
            live = self.live_counts()
        answer = self.answers.get(m.actor_id)
        data = {
            "control_rev": self.control_rev, "phase": self.phase, "finish_reason": self.finish_reason,
            "pause_reason": self.pause_reason, "editing": False, "host_connected": self.host.conn is not None,
            "title": self.quiz["title"] if self.quiz else None,
            "question_index": self.index, "question_count": len(self.quiz["questions"]) if self.quiz else 0,
            "current_question": current, "remaining_ms": self.remaining(),
            "self": {"actor_id": m.actor_id, "role": m.role, "username": m.username,
                     "total_score": None if host else m.total_score,
                     "submitted_choice_id": None if host or not answer else answer["choice_id"]},
            "leaderboard": self.leaderboard(), "last_result": last, "live": live,
        }
        if host:
            data["quiz"] = self.quiz
            data["participants"] = [{"player_id": p.actor_id, "username": p.username,
                                     "connected": p.conn is not None, "participation": p.participation,
                                     "total_score": p.total_score} for p in self.players.values()]
        return {"v": 1, "type": "STATE", "room_id": self.room_id, "state_rev": self.state_rev, "data": data}

    def live_counts(self) -> dict:
        eligible = self.active_players()
        return {"answered_count": sum(1 for p in eligible if p.actor_id in self.answers),
                "eligible_count": len(eligible)}

    def broadcast(self) -> None:
        """Queue a separately filtered STATE for the host and every attached ACTIVE player."""
        for m in [self.host] + self.active_players():
            if m.conn is not None:
                m.conn.send(self.state_for(m))

    def open_question(self, index: int) -> None:
        self.index = index
        self.attempt_id = new_id()
        self.deadline_ms = now_ms() + self.question()["time_limit_ms"]
        self.answers = {}
        self.phase = "OPEN"
        self.control_rev += 1
        self.state_rev += 1

    def close_question(self, reason: str) -> None:
        """Score every accepted answer exactly once and move to RESULTS."""
        q = self.question()
        self.own_results = {}
        for p in self.players.values():
            answer = self.answers.get(p.actor_id)
            correct = bool(answer) and answer["choice_id"] == q["correct_choice_id"]
            points = score(answer["remaining_ms"], q["time_limit_ms"]) if correct else 0
            p.total_score += points
            self.own_results[p.actor_id] = {"choice_id": answer["choice_id"] if answer else None,
                                            "correct": correct, "points": points, "total_score": p.total_score}
        self.last_result = {"question_id": q["question_id"], "attempt_id": self.attempt_id,
                            "correct_choice_id": q["correct_choice_id"], "reason": reason}
        self.phase = "RESULTS"
        self.control_rev += 1
        self.state_rev += 1
        self.broadcast()
        log(f"room {self.pin}", f"question {self.index + 1} closed ({reason}), "
                                f"{len(self.answers)} answers -> STATE RESULTS")

    def close_if_due(self) -> None:
        if self.phase == "OPEN" and now_ms() >= self.deadline_ms:
            self.close_question("TIMEOUT")

    def close_if_all_answered(self) -> None:
        eligible = self.active_players()
        if self.phase == "OPEN" and eligible and all(p.actor_id in self.answers for p in eligible):
            self.close_question("ALL_ANSWERED")


class RoomManager:
    def __init__(self, server, interval: float):
        self.server = server
        self.interval = interval
        self.lock = threading.Lock()          # registry lock
        self.by_pin: Dict[str, Room] = {}
        self.owners: Dict[str, Room] = {}     # host key -> its unfinished room
        self.ledger: Dict[tuple, tuple] = {}  # (host key, request_id) -> (fingerprint, room_id)

    # -- unattached requests ----------------------------------------------

    def create(self, conn, msg: dict, who: str) -> Optional[Member]:
        data, rid = msg["data"], msg["request_id"]
        name = unicodedata.normalize("NFC", data["username"]).strip()
        if not 1 <= len(name) <= 32:
            conn.send(_error("INVALID_USERNAME", "names must be 1..32 characters", rid))
            return None
        raw = base64.urlsafe_b64decode(data["host_token"] + "=")
        key = hashlib.sha256(raw).hexdigest()   # the server never stores the token itself as an index
        fingerprint = encode_json(data)
        with self.lock:
            earlier = self.ledger.get((key, rid))
            if earlier is not None:                          # ledger before ownership
                if earlier[0] == fingerprint:
                    conn.send(_error("ROOM_ALREADY_CREATED", "this request already created a room", rid,
                                     existing_room_id=earlier[1]))
                else:
                    conn.send(_error("REQUEST_ID_REUSED", "request_id reused with different content", rid))
                return None
            owned = self.owners.get(key)
            if owned is not None:
                log(who, f"CREATE_ROOM while room {owned.pin} is unfinished -> HOST_HAS_ROOM")
                conn.send(_error("HOST_HAS_ROOM", "finish or close your current room first", rid,
                                 existing_room_id=owned.room_id))
                return None
            pin = self._free_pin()
            room = Room(self, pin, conn, name, data["host_token"], key)
            self.by_pin[pin] = room
            self.owners[key] = room
            self.ledger[(key, rid)] = (fingerprint, room.room_id)
            with room.lock:                                  # registry -> room order
                room.state_rev = 1
                host = room.host
                conn.send(_result(rid, room.room_id, {
                    "room_id": room.room_id, "pin": pin, "session_id": host.session_id,
                    "actor_id": host.actor_id, "role": "HOST", "reconnect_token": host.token}))
                conn.send(room.state_for(host))
        threading.Thread(target=self._timer, args=(room,), daemon=True).start()
        log(who, f"CREATE_ROOM {name!r} -> room {pin} created")
        return host

    def _free_pin(self) -> str:
        while True:
            pin = f"{secrets.randbelow(1_000_000):06d}"
            if pin not in self.by_pin and pin != self.server.pin:
                return pin

    def join(self, conn, msg: dict, who: str) -> Optional[Member]:
        data, rid = msg["data"], msg["request_id"]
        with self.lock:
            room = self.by_pin.get(data["pin"]) if is_pin(data["pin"]) else None
        if room is None:
            log(who, f"JOIN_ROOM wrong PIN {data['pin']!r} -> INVALID_PIN")
            conn.send(_error("INVALID_PIN", "no room has that PIN", rid))
            return None
        name = unicodedata.normalize("NFC", data["username"]).strip()
        with room.lock:
            # Report order: PIN, finished room, name format, name taken, room full.
            if room.phase == "FINISHED":
                code, text = "ROOM_FINISHED", "this room has finished"
            elif not 1 <= len(name) <= 32:
                code, text = "INVALID_USERNAME", "names must be 1..32 characters"
            elif name.casefold() in room.names:
                code, text = "NAME_TAKEN", "that name is already used in this room"
            elif len(room.active_players()) >= MAX_PLAYERS:   # disconnected ACTIVE players still count
                code, text = "ROOM_FULL", f"the room already has {MAX_PLAYERS} players"
            else:
                code = None
            if code:
                conn.send(_error(code, text, rid))
            else:
                m = Member(room, conn, "PLAYER", name, base64.urlsafe_b64encode(secrets.token_bytes(32))
                           .rstrip(b"=").decode())
                room.players[m.actor_id] = m
                room.names.add(name.casefold())
                room.state_rev += 1
                conn.send(_result(rid, room.room_id, {
                    "room_id": room.room_id, "pin": room.pin, "session_id": m.session_id,
                    "actor_id": m.actor_id, "role": "PLAYER", "reconnect_token": m.token}))
                room.broadcast()                                 # joiner: RESULT then STATE
                count = len(room.active_players())
        if code:
            log(who, f"JOIN_ROOM room {room.pin} {name!r} -> {code}")
            return None
        log(who, f"JOIN_ROOM room {room.pin} {name!r} joined ({count}/{MAX_PLAYERS})")
        return m

    # -- requests from an attached member ---------------------------------

    def handle(self, m: Member, msg: dict, who: str) -> None:
        mtype = msg["type"]
        if mtype == "NEXT_QUESTION":
            with self.lock:                  # may release ownership: registry lock first
                with m.room.lock:
                    outcome = self._handle_locked(m, msg)
        else:
            with m.room.lock:
                outcome = self._handle_locked(m, msg)
        log(who, f"room {m.room.pin} {m.role.lower()} {mtype} -> {outcome}")

    def _handle_locked(self, m: Member, msg: dict) -> str:
        room, conn = m.room, m.conn
        mtype, rid, data = msg["type"], msg["request_id"], msg["data"]

        def fail(code, text, **extra):
            conn.send(_error(code, text, rid, room.room_id, **extra))
            return code

        room.close_if_due()                  # an overdue question closes before anything else
        if mtype == "GET_STATE":
            conn.send(_result(rid, room.room_id, {}))
            conn.send(room.state_for(m, recovery=True))
            return "RESULT + STATE"
        if mtype in ("SUBMIT_ANSWER", "LEAVE_ROOM"):
            if m.role != "PLAYER":
                return fail("NOT_PLAYER", f"{mtype} is a player request")
            return self._submit(m, msg, fail) if mtype == "SUBMIT_ANSWER" else self._leave(m, rid)
        if mtype not in ("PUT_QUIZ", "START_QUIZ", "CLOSE_QUESTION", "NEXT_QUESTION"):
            if m.role != "HOST" and mtype in self.server.host_types:
                return fail("NOT_HOST", f"{mtype} is a host command")
            return fail("INVALID_STATE", f"{mtype} {NOT_SUPPORTED}")
        if m.role != "HOST":
            return fail("NOT_HOST", f"{mtype} is a host command")
        if data["expected_control_rev"] != room.control_rev:
            fail("STALE_STATE", "the room changed; check the new STATE", current_control_rev=room.control_rev)
            conn.send(room.state_for(m))
            return "STALE_STATE + fresh STATE"

        if mtype == "PUT_QUIZ":
            if room.phase != "LOBBY":
                return fail("INVALID_STATE", "quizzes can be loaded only in the lobby")
            try:
                validate_quiz(data["title"], data["questions"])
            except ProtocolError as exc:
                return fail(exc.code, exc.detail)
            room.quiz = {"title": data["title"], "questions": data["questions"]}
            room.control_rev += 1
            room.state_rev += 1
            conn.send(_result(rid, room.room_id, {}))
            room.broadcast()
            return f"quiz loaded ({len(data['questions'])} questions)"
        if mtype == "START_QUIZ":
            if room.phase != "LOBBY":
                return fail("INVALID_STATE", "the quiz has already started")
            if room.quiz is None:
                return fail("INVALID_STATE", "load a quiz first")
            connected = [p for p in room.active_players() if p.conn is not None]
            if len(connected) < START_GATE:
                return fail("NOT_ENOUGH_PLAYERS", f"{len(connected)} connected players; {START_GATE} needed")
            room.open_question(0)
            conn.send(_result(rid, room.room_id, {}))
            room.broadcast()
            return f"question 1 OPEN for {len(room.active_players())} players"
        if mtype == "CLOSE_QUESTION":
            if room.phase not in ("OPEN", "PAUSED"):
                return fail("INVALID_STATE", "no question is open")
            conn.send(_result(rid, room.room_id, {}))
            room.close_question("HOST_CLOSED")
            return "closed by host"
        # NEXT_QUESTION
        if room.phase != "RESULTS":
            return fail("INVALID_STATE", "the current question has not closed")
        conn.send(_result(rid, room.room_id, {}))
        if room.index + 1 < len(room.quiz["questions"]):
            room.open_question(room.index + 1)
            room.broadcast()
            return f"question {room.index + 1} OPEN"
        room.phase, room.finish_reason = "FINISHED", "COMPLETED"
        room.control_rev += 1
        room.state_rev += 1
        if self.owners.get(room.host_key) is room:      # release only this room's ownership
            del self.owners[room.host_key]
        room.broadcast()
        return "FINISHED (COMPLETED), host may create a new room"

    def _submit(self, m: Member, msg: dict, fail) -> str:
        room, data, rid = m.room, msg["data"], msg["request_id"]
        answer = room.answers.get(m.actor_id)
        q = room.question()
        if answer and answer["request_id"] == rid and answer["data"] == data:
            m.conn.send(_result(rid, room.room_id, {"accepted": True, **{k: data[k] for k in (
                "question_id", "attempt_id", "choice_id")}}))
            return "replay -> original RESULT"
        if q is None:
            return fail("INVALID_STATE", "no question has started")
        if data["question_id"] != q["question_id"] or data["attempt_id"] != room.attempt_id:
            fail("STALE_ATTEMPT", "that question attempt is not current")
            m.conn.send(room.state_for(m))
            return "STALE_ATTEMPT"
        if room.phase in ("RESULTS", "FINISHED"):
            return fail("ANSWER_LATE", "the question has closed")
        if room.phase == "PAUSED":
            return fail("QUESTION_PAUSED", "the question is paused")
        if data["choice_id"] not in [c["choice_id"] for c in q["choices"]]:
            return fail("INVALID_CHOICE", "not a choice of this question")
        if answer:
            return fail("ALREADY_ANSWERED", "one answer per question", question_id=q["question_id"],
                        attempt_id=room.attempt_id, choice_id=answer["choice_id"])
        remaining = room.deadline_ms - now_ms()
        room.answers[m.actor_id] = {"choice_id": data["choice_id"], "remaining_ms": remaining,
                                    "request_id": rid, "data": dict(data)}
        m.conn.send(_result(rid, room.room_id, {"accepted": True, "question_id": q["question_id"],
                                                "attempt_id": room.attempt_id, "choice_id": data["choice_id"]}))
        room.close_if_all_answered()
        return f"accepted ({remaining} ms left)"

    def _leave(self, m: Member, rid: str) -> str:
        room = m.room
        m.participation = "LEFT"          # token revoked: LEFT identities can never resume
        room.state_rev += 1
        m.conn.send(_result(rid, room.room_id, {"left": True}))
        m.conn.close()
        m.conn = None
        self._forget_binding(m)
        room.broadcast()
        room.close_if_all_answered()
        return f"LEFT ({len(room.active_players())}/{MAX_PLAYERS} active)"

    def disconnected(self, m: Member) -> None:
        """TCP connection ended without LEAVE_ROOM: the identity stays ACTIVE and keeps its slot."""
        room = m.room
        with room.lock:
            if m.conn is None:
                return
            m.conn = None
            self._forget_binding(m)
            room.state_rev += 1
            if m.role == "HOST":
                room.close_if_due()            # an expired question closes; it is not paused afterwards
                if room.phase == "OPEN":
                    room.frozen_remaining = room.remaining()
                    room.phase, room.pause_reason = "PAUSED", "HOST_DISCONNECTED"
                    room.control_rev += 1
                room.broadcast()
            elif room.host.conn is not None:
                room.host.conn.send(room.state_for(room.host))   # only the host needs this update
        log(f"room {room.pin}", f"{m.role.lower()} {m.username!r} disconnected (still ACTIVE)")

    def _forget_binding(self, m: Member) -> None:
        if m.binding:
            self.server.forget_binding(m.binding["binding_id"])
            m.binding = None

    # -- room timer thread -------------------------------------------------

    def _timer(self, room: Room) -> None:
        """Closes questions at their deadline and sends LIVE_STATUS about once per interval."""
        while True:
            sends = []
            with room.lock:
                room.close_if_due()
                if room.phase == "FINISHED":
                    return
                wait = self.interval
                if room.phase in ("OPEN", "PAUSED"):
                    counts, q = room.live_counts(), room.question()
                    if room.phase == "OPEN":
                        wait = min(wait, max(0.0, (room.deadline_ms - now_ms()) / 1000))
                    for m in [room.host] + room.active_players():
                        b = m.binding
                        if m.conn is None or not b or not b["confirmed"]:
                            continue
                        b["seq"] += 1
                        sends.append((udp.encode_datagram({
                            "v": 1, "type": "LIVE_STATUS", "room_id": room.room_id, "session_id": m.session_id,
                            "binding_id": b["binding_id"], "question_id": q["question_id"],
                            "attempt_id": room.attempt_id, "control_rev": room.control_rev, "seq": b["seq"],
                            "data": {"remaining_ms": room.remaining(), **counts}}), b["udp_addr"]))
            for payload, addr in sends:                       # socket I/O outside the lock
                self.server.send_udp(payload, addr)
            time.sleep(max(0.01, wait))
