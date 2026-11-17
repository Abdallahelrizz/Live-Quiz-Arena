"""Builds the STATE snapshot for one recipient (report section 2.3, "STATE data").

STATE is filtered per recipient, not only per role:

  * a player gets choice IDs and symbol positions only: no question text, no
    answer text and no correct answer while the question is open;
  * the host also gets the texts, the whole quiz and the participant list;
  * `self` and `last_result.own_result` belong to that one recipient.

The function only reads the room. The caller must hold the room lock, so the
snapshot comes from one committed state and never mixes two moments.
"""

from __future__ import annotations

from fatrat.common import now_ms
from fatrat.server.scoring import leaderboard


def remaining_ms(room) -> int:
    """Time left on the current question: live in OPEN, frozen in PAUSED, otherwise 0."""
    if room.phase == "OPEN":
        return max(0, room.deadline_ms - now_ms())
    return room.frozen_remaining if room.phase == "PAUSED" else 0


def live_counts(room) -> dict:
    """Answered and eligible counts for the current attempt.

    Eligible = every ACTIVE player, connected or not. Players who left or
    were removed are not counted, even if their answer was accepted earlier,
    so answered_count can never exceed eligible_count.
    """
    eligible = room.active_players()
    return {"answered_count": sum(1 for p in eligible if p.actor_id in room.answers),
            "eligible_count": len(eligible)}


def state_for(room, member, recovery: bool = False) -> dict:
    """Return the complete STATE message for `member`.

    recovery=True adds the live counts; it is used for GET_STATE and for the
    STATE sent right after joining or resuming, when the client may have
    missed UDP snapshots.
    """
    host = member.role == "HOST"
    q = room.current
    current = None
    if q is not None:
        current = {"question_id": q["question_id"], "attempt_id": room.attempt_id,
                   "time_limit_ms": q["time_limit_ms"],
                   "choices": [{"choice_id": c["choice_id"], "symbol_index": i}
                               for i, c in enumerate(q["choices"])]}
        if host:   # only the host (shared display) receives the texts
            current["text"] = q["text"]
            for shown, full in zip(current["choices"], q["choices"]):
                shown["text"] = full["text"]

    last = None
    if room.last_result is not None:
        own = None
        if not host:
            # A player who joined after that question closed has no entry: show zero points.
            own = room.own_results.get(member.actor_id) or {
                "choice_id": None, "correct": False, "points": 0, "total_score": member.total_score}
        last = {**room.last_result, "own_result": own}

    live = None
    if recovery and room.phase in ("OPEN", "PAUSED"):
        live = live_counts(room)

    answer = room.answers.get(member.actor_id)
    if room.phase == "FINISHED":
        count = room.finished_count          # frozen history, not the editable quiz
    else:
        count = len(room.quiz["questions"]) if room.quiz else 0
    data = {
        "control_rev": room.control_rev,
        "phase": room.phase,
        "finish_reason": room.finish_reason,
        "pause_reason": room.pause_reason,
        "editing": room.phase == "PAUSED" and room.pause_reason == "EDITING",
        "host_connected": room.host.conn is not None,
        "title": room.quiz["title"] if room.quiz else None,
        "question_index": room.index,
        "question_count": count,
        "current_question": current,
        "remaining_ms": remaining_ms(room),
        "self": {"actor_id": member.actor_id, "role": member.role, "username": member.username,
                 "total_score": None if host else member.total_score,
                 "submitted_choice_id": None if host or not answer else answer["choice_id"]},
        "leaderboard": room.final_leaderboard if room.final_leaderboard is not None else leaderboard(
            (p.total_score, p.username, p.actor_id, p.participation) for p in room.players.values()),
        "last_result": last,
        "live": live,
    }
    if host:
        data["quiz"] = room.quiz
        data["participants"] = [
            {"player_id": p.actor_id, "username": p.username, "connected": p.conn is not None,
             "participation": p.participation, "total_score": p.total_score}
            for p in room.players.values()]
    return {"v": 1, "type": "STATE", "room_id": room.room_id, "state_rev": room.state_rev, "data": data}
