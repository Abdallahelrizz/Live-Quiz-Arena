"""What the terminal host and the terminal player share: wording and screens.

Nothing here uses the network. Screen receives ClientSession events and
prints them. Events arrive on the session's threads while the main thread
waits in input(), so every print goes through fatrat.common.say, whose lock
keeps lines from mixing.
"""

from __future__ import annotations

import threading
from typing import Optional

from fatrat.common import say

# The eight answer symbols, by choice position (symbol_index 0..7). Players
# see only these, never the answer text, exactly as on the shared screen.
SYMBOLS = ("Cheddar", "Brie", "Gouda", "Swiss", "Feta", "Mozzarella", "Parmesan", "Blue")

FRIENDLY = {
    "INVALID_PIN": "No room has that PIN.",
    "INVALID_USERNAME": "Names must have 1 to 32 characters.",
    "NAME_TAKEN": "That name is already used in this room. Choose another.",
    "ROOM_FULL": "This room already has 50 players.",
    "ROOM_FINISHED": "That quiz has already finished.",
    "NOT_ENOUGH_PLAYERS": "At least 5 connected players are needed to start.",
    "ALREADY_ANSWERED": "You have already answered this question.",
    "ANSWER_LATE": "Too late: the question has closed.",
    "QUESTION_PAUSED": "The question is paused. Wait for the host.",
    "INVALID_CHOICE": "That is not one of the choices.",
    "STALE_STATE": "The room changed just before your command. Check the screen and try again.",
    "STALE_ATTEMPT": "That question is no longer the current one.",
    "INVALID_QUIZ": "The quiz was refused",
    "INVALID_STATE": "That is not possible right now",
    "QUESTION_LOCKED": "That question has already been played and cannot be changed.",
    "QUESTION_LIMIT": "A quiz must keep between 1 and 30 questions.",
    "RATE_LIMITED": "Too many requests at once. Wait a moment and try again.",
}


def explain(exc) -> str:
    """Turn a RequestFailed into one line for the user."""
    text = FRIENDLY.get(exc.code)
    if text is None:
        return f"{exc.code}: {exc.message}"
    return text if text.endswith(".") else f"{text}: {exc.message}."


def seconds(ms: int) -> str:
    return f"{(ms + 999) // 1000} s"


def print_leaderboard(rows: list, me: Optional[str] = None, limit: int = 10) -> None:
    """Print rank rows. Players with equal scores share one rank."""
    shown = 0
    for row in rows:
        for player in row["players"]:
            if shown == limit:
                say("    ...")
                return
            mark = "  <- you" if player["player_id"] == me else ""
            tag = "" if player["participation"] == "ACTIVE" else f" ({player['participation'].lower()})"
            say(f"    {row['rank']:>2}. {player['username']}{tag}  {row['score']}{mark}")
            shown += 1


class Screen:
    """Prints what changed. Remembers the last thing shown so it is not printed twice."""

    def __init__(self, session, role: str):
        self.session, self.role = session, role
        self._shown = None
        self._last_second = None
        self._roster = None
        self._host_connected = True
        # Events come from the TCP reader and the UDP receiver. This lock keeps
        # one event's lines together (a question is several lines).
        self._lock = threading.Lock()

    def on_event(self, kind: str, payload) -> None:
        with self._lock:
            self._show(kind, payload)

    def _show(self, kind: str, payload) -> None:
        if kind == "state":
            self._state(payload)
        elif kind == "live":
            self._live(payload)
        elif kind == "connection":
            if payload == "lost":
                say("\n  ! Connection to the server lost.")
            elif payload == "reconnecting":
                say("  ... trying to reconnect")
            elif payload == "resumed":
                say("  Reconnected. Your place and score were kept.")
        elif kind == "notice":
            if payload["type"] == "REMOVED":
                say(f"\n  The host removed you: {payload['message']}  (press Enter to exit)")
            elif payload["type"] == "SESSION_REPLACED":
                say("\n  This session was opened somewhere else; this window is no longer connected.")
            else:
                say(f"\n  Cannot rejoin: {payload['message']}")

    # -- STATE ------------------------------------------------------------

    def _state(self, s: dict) -> None:
        host = self.role == "HOST"
        if host and s["phase"] == "LOBBY":
            roster = [(p["username"], p["connected"]) for p in s["participants"]
                      if p["participation"] == "ACTIVE"]
            if roster != self._roster:
                self._roster = roster
                names = ", ".join(n if c else f"{n} (offline)" for n, c in roster) or "nobody yet"
                say(f"  Players ({len(roster)}): {names}")
        if s["host_connected"] != self._host_connected and s["phase"] != "FINISHED" and not host:
            say("  The host is back." if s["host_connected"] else "  The host lost its connection. Please wait.")
        self._host_connected = s["host_connected"]
        q = s["current_question"]
        key = (s["phase"], s["pause_reason"], q["attempt_id"] if q else None,
               s["title"] if s["phase"] == "LOBBY" else None)
        if key == self._shown:
            return
        self._shown, self._last_second = key, None
        phase = s["phase"]
        if phase == "LOBBY":
            if s["title"]:
                say(f"  Quiz loaded: {s['title']} ({s['question_count']} questions)")
            elif not host:
                say("  You are in. Waiting for the host to start...")
        elif phase == "OPEN":
            self._question(s, q, host)
        elif phase == "PAUSED":
            why = {"MANUAL": "the host paused the quiz", "EDITING": "the host is editing this question",
                   "HOST_DISCONNECTED": "the host is disconnected"}[s["pause_reason"]]
            say(f"\n  || Paused: {why}. {seconds(s['remaining_ms'])} left on the clock.")
        elif phase == "RESULTS":
            self._results(s, q, host)
        elif phase == "FINISHED":
            ending = "The quiz is over." if s["finish_reason"] == "COMPLETED" else "The host closed the room."
            say(f"\n  ===== {ending} Final ranking =====")
            print_leaderboard(s["leaderboard"], s["self"]["actor_id"], limit=60)
            if not host:
                say(f"  Your final score: {s['self']['total_score']}")

    def _question(self, s: dict, q: dict, host: bool) -> None:
        say(f"\n  ----- Question {s['question_index'] + 1} of {s['question_count']} "
            f"({seconds(q['time_limit_ms'])}) -----")
        if host:
            say(f"  {q['text']}")
        for choice in q["choices"]:
            label = f"    {choice['symbol_index'] + 1}. {SYMBOLS[choice['symbol_index']]}"
            say(label + (f"  -  {choice['text']}" if host else ""))
        if not host:
            if s["self"]["submitted_choice_id"]:
                say("  Your answer is already in. Waiting for the result...")
            else:
                say(f"  Type a number 1-{len(q['choices'])} and press Enter.  "
                    f"{seconds(s['remaining_ms'])} left.")

    def _results(self, s: dict, q: dict, host: bool) -> None:
        result = s["last_result"]
        position = [c["choice_id"] for c in q["choices"]].index(result["correct_choice_id"])
        why = {"ALL_ANSWERED": "everyone answered", "TIMEOUT": "time is up",
               "HOST_CLOSED": "closed by the host"}[result["reason"]]
        say(f"\n  ----- Results ({why}) -----")
        correct = f"{position + 1}. {SYMBOLS[position]}"
        say(f"  Correct answer: {correct}" + (f"  -  {q['choices'][position]['text']}" if host else ""))
        own = result["own_result"]
        if own is not None:
            if own["choice_id"] is None:
                say("  You did not answer: 0 points.")
            else:
                say(f"  {'Correct!' if own['correct'] else 'Wrong.'}  +{own['points']} points.")
            say(f"  Your total: {own['total_score']}")
        print_leaderboard(s["leaderboard"], s["self"]["actor_id"])
        if host:
            last = s["question_index"] + 1 == s["question_count"]
            say("  Type 'next' " + ("to finish and show the final ranking." if last else "for the next question."))

    # -- LIVE_STATUS (UDP) --------------------------------------------------

    def _live(self, live: dict) -> None:
        left = (live["remaining_ms"] + 999) // 1000
        if left == self._last_second:
            return
        self._last_second = left
        if self.role == "HOST":
            say(f"    {left:>3} s left   answered {live['answered_count']}/{live['eligible_count']}")
        elif left in (30, 20, 10, 5, 3, 2, 1):
            say(f"    {left} s left")
