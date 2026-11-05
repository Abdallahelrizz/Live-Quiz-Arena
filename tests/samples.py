"""Builders for valid sample messages, used by several test files.

Every builder returns a fresh, valid message, so a test can break exactly one
thing (remove a field, change a type) and check that validation notices.
"""

from __future__ import annotations

import base64
import os
import uuid

from fatrat.protocol.catalogue import CATALOGUE, REQUEST, UNATTACHED_REQUESTS


def new_id() -> str:
    return str(uuid.uuid4())


def new_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def question(choices: int = 4, time_limit_ms: int = 20_000, text: str = "Which layer does TCP belong to?") -> dict:
    ids = [new_id() for _ in range(choices)]
    return {
        "question_id": new_id(),
        "text": text,
        "choices": [{"choice_id": cid, "text": f"Choice {i + 1}"} for i, cid in enumerate(ids)],
        "correct_choice_id": ids[0],
        "time_limit_ms": time_limit_ms,
    }


def sample_value(kind: str):
    """A valid value for a catalogue field type."""
    kind = kind.replace("|null", "")
    if kind.startswith("enum:"):
        return kind[len("enum:"):].split(",")[0]
    return {
        "int": 3,
        "str": "Mr Fat Rat",
        "bool": False,
        "true": True,
        "uuid": new_id(),
        "token": new_token(),
        "nonce": os.urandom(16).hex(),
        "pin": "042917",
        "port": 43001,
        "question": question(),
        "question_list": [question(), question()],
        "uuid_list": [new_id(), new_id()],
    }[kind]


def client_message(mtype: str) -> dict:
    """A valid client-to-server message of any client type in the catalogue."""
    spec = CATALOGUE[mtype]
    message = {"v": 1, "type": mtype, "data": {k: sample_value(t) for k, t in spec.data.items()}}
    if spec.envelope == REQUEST:
        message["request_id"] = new_id()
        if mtype not in UNATTACHED_REQUESTS:
            message["room_id"] = new_id()
    elif mtype == "UDP_HELLO":
        message["session_id"] = new_id()
        message["binding_id"] = new_id()
    return message


def live_status(**overrides) -> dict:
    message = {
        "v": 1, "type": "LIVE_STATUS",
        "room_id": new_id(), "session_id": new_id(), "binding_id": new_id(),
        "question_id": new_id(), "attempt_id": new_id(), "control_rev": 1, "seq": 1,
        "data": {"remaining_ms": 9000, "answered_count": 0, "eligible_count": 5},
    }
    message.update(overrides)
    return message


def player_state(phase: str = "OPEN") -> dict:
    """A valid player STATE in LOBBY, OPEN or RESULTS."""
    me, other = new_id(), new_id()
    q_id, attempt, choices = new_id(), new_id(), [new_id() for _ in range(4)]
    current = None
    if phase != "LOBBY":
        current = {"question_id": q_id, "attempt_id": attempt, "time_limit_ms": 20_000,
                   "choices": [{"choice_id": c, "symbol_index": i} for i, c in enumerate(choices)]}
    score = 870 if phase == "RESULTS" else 0
    if score:
        leaderboard = [
            {"rank": 1, "score": score, "players": [{"player_id": me, "username": "abdallah", "participation": "ACTIVE"}]},
            {"rank": 2, "score": 0, "players": [{"player_id": other, "username": "rat", "participation": "ACTIVE"}]},
        ]
    else:
        leaderboard = [{"rank": 1, "score": 0, "players": [
            {"player_id": me, "username": "abdallah", "participation": "ACTIVE"},
            {"player_id": other, "username": "rat", "participation": "ACTIVE"}]}]
    last_result = None
    if phase == "RESULTS":
        last_result = {"question_id": q_id, "attempt_id": attempt, "correct_choice_id": choices[0],
                       "reason": "TIMEOUT",
                       "own_result": {"choice_id": choices[0], "correct": True, "points": 870, "total_score": 870}}
    return {
        "v": 1, "type": "STATE", "room_id": new_id(), "state_rev": 4,
        "data": {
            "control_rev": 1, "phase": phase, "finish_reason": None, "pause_reason": None,
            "editing": False, "host_connected": True, "title": "Networks quiz",
            "question_index": None if phase == "LOBBY" else 0, "question_count": 3,
            "current_question": current, "remaining_ms": 12_000 if phase == "OPEN" else 0,
            "self": {"actor_id": me, "role": "PLAYER", "username": "abdallah",
                     "total_score": score, "submitted_choice_id": None},
            "leaderboard": leaderboard, "last_result": last_result, "live": None,
        },
    }
