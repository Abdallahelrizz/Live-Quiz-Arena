"""Validation of Fat-Rat protocol messages (report sections 2.2 and 2.3).

The server calls validate_message() on every decoded message before acting
on it. A failure raises ProtocolError carrying the error code to send back,
whether the connection must close (fatal) and the request_id to correlate
the ERROR with (reply_to).

Clients (and the protocol check client) use the same function with
from_server=True to check what the server sent, plus validate_result() and
validate_state() for the request-specific RESULT data and STATE snapshots.

Rules implemented here, from the report:
  * every message has v == 1, a string type and an object data;
    an integer v other than 1 is UNSUPPORTED_VERSION and fatal;
  * the type must exist in the catalogue, travel on its catalogue transport
    and come from the right side (a client may not send STATE, etc.);
  * required envelope and data fields must be present with the right type;
  * unknown fields from a client are rejected; clients ignore unknown
    server extension fields (strict=True turns that leniency off for tests);
  * schema errors are nonfatal INVALID_MESSAGE, correlated by request_id
    when the message has a usable one, otherwise reply_to = null.
"""

from __future__ import annotations

import base64
import re
import uuid
from typing import Any, Callable, Dict, Optional

from .catalogue import (
    CATALOGUE, CLOSE_REASONS, ERROR_CODES, ERROR_OPTIONAL_EXTRA, ERROR_REQUIRED_EXTRA,
    FINISH_REASONS, LIVE_STATUS_KIND, MAX_CHOICE_TEXT, MAX_CHOICES, MAX_QUESTION_TEXT,
    MAX_QUESTIONS, MAX_QUIZ_BYTES, MAX_TIME_MS, MAX_TITLE, MIN_CHOICES, MIN_QUESTIONS,
    MIN_TIME_MS, NOTICE, PARTICIPATION, PAUSE_REASONS, PHASES, PING_KIND, PROTOCOL_VERSION,
    REPLY, REQUEST, RESULT_DATA, RETRYABLE_ERROR_CODES, STATE_KIND, UDP_HELLO_KIND,
    UNATTACHED_REQUESTS, MessageSpec,
)
from .framing import encode_json

MAX_INT = 2 ** 63 - 1


class ProtocolError(Exception):
    """A message broke a protocol rule.

    code     error code for the ERROR reply (INVALID_MESSAGE, UNSUPPORTED_VERSION,
             INVALID_QUIZ, ...)
    fatal    True if the connection must be closed after the reply
    reply_to request_id to put in the ERROR, or None when none is usable
    """

    def __init__(self, code: str, detail: str, *, fatal: bool = False, reply_to: Optional[str] = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.fatal = fatal
        self.reply_to = reply_to


# --------------------------------------------------------------------------
# Primitive value checks (each returns True when the value is acceptable)
# --------------------------------------------------------------------------

def is_int(value: Any) -> bool:
    """Nonnegative integer within 2**63 - 1. type() is used because bool is a subclass of int."""
    return type(value) is int and 0 <= value <= MAX_INT


def is_uuid(value: Any) -> bool:
    """Canonical lowercase hyphenated UUID version 4."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value and parsed.version == 4


_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_-]{43}")


def is_token(value: Any) -> bool:
    """43-character unpadded URL-safe Base64 that decodes to 32 bytes and re-encodes identically."""
    if not isinstance(value, str) or not _TOKEN_CHARS.fullmatch(value):
        return False
    raw = base64.urlsafe_b64decode(value + "=")
    return len(raw) == 32 and base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == value


def is_nonce(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def is_pin(value: Any) -> bool:
    """Exactly six ASCII digits (leading zeros allowed)."""
    return isinstance(value, str) and re.fullmatch(r"[0-9]{6}", value) is not None


def is_question_shape(value: Any) -> bool:
    """Question object with exactly the documented fields and field types.

    Content rules (choice count, lengths, time range, ...) are checked by
    validate_question() and produce INVALID_QUIZ instead of INVALID_MESSAGE.
    """
    if not isinstance(value, dict) or set(value) != {
            "question_id", "text", "choices", "correct_choice_id", "time_limit_ms"}:
        return False
    if not (is_uuid(value["question_id"]) and isinstance(value["text"], str)
            and is_uuid(value["correct_choice_id"]) and is_int(value["time_limit_ms"])
            and isinstance(value["choices"], list)):
        return False
    return all(
        isinstance(c, dict) and set(c) == {"choice_id", "text"}
        and is_uuid(c["choice_id"]) and isinstance(c["text"], str)
        for c in value["choices"]
    )


_CHECKS: Dict[str, Callable[[Any], bool]] = {
    "int": is_int,
    "str": lambda v: isinstance(v, str),
    "bool": lambda v: isinstance(v, bool),
    "true": lambda v: v is True,
    "uuid": is_uuid,
    "token": is_token,
    "nonce": is_nonce,
    "pin": is_pin,
    "port": lambda v: type(v) is int and 1 <= v <= 65535,
    "object": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, list),
    "question": is_question_shape,
    "question_list": lambda v: isinstance(v, list) and all(is_question_shape(q) for q in v),
    "uuid_list": lambda v: isinstance(v, list) and all(is_uuid(x) for x in v),
}


def check_value(kind: str, value: Any) -> bool:
    """Check one value against a field type from the catalogue."""
    if kind.endswith("|null"):
        return value is None or check_value(kind[: -len("|null")], value)
    if kind.startswith("enum:"):
        return isinstance(value, str) and value in kind[len("enum:"):].split(",")
    return _CHECKS[kind](value)


def _check_fields(obj: dict, required: Dict[str, str], optional: Dict[str, str],
                  always_allowed: set, strict: bool, where: str, fail) -> None:
    for name, kind in required.items():
        if name not in obj:
            fail(f"missing {where} field '{name}'")
        if not check_value(kind, obj[name]):
            fail(f"{where} field '{name}' must be {kind}")
    for name, kind in optional.items():
        if name in obj and not check_value(kind, obj[name]):
            fail(f"{where} field '{name}' must be {kind}")
    if strict:
        unknown = set(obj) - set(required) - set(optional) - always_allowed
        if unknown:
            fail(f"unknown {where} field(s): {', '.join(sorted(unknown))}")


def _envelope_fields(spec: MessageSpec):
    """Return (required, optional) top-level fields for this message type."""
    if spec.envelope == REQUEST:
        if spec.name in UNATTACHED_REQUESTS:
            return {"request_id": "uuid"}, {}
        return {"request_id": "uuid", "room_id": "uuid"}, {}
    if spec.envelope == REPLY:
        reply_to = "uuid|null" if spec.name == "ERROR" else "uuid"
        return {"reply_to": reply_to}, {"room_id": "uuid"}
    if spec.envelope == STATE_KIND:
        return {"room_id": "uuid", "state_rev": "int"}, {}
    if spec.envelope == PING_KIND:
        return {}, {}
    if spec.envelope == NOTICE:
        return {}, {"room_id": "uuid"}
    if spec.envelope == UDP_HELLO_KIND:
        return {"session_id": "uuid", "binding_id": "uuid"}, {}
    if spec.envelope == LIVE_STATUS_KIND:
        ids = ("room_id", "session_id", "binding_id", "question_id", "attempt_id")
        return {**{k: "uuid" for k in ids}, "control_rev": "int", "seq": "int"}, {}
    raise AssertionError(f"unknown envelope kind {spec.envelope}")


# --------------------------------------------------------------------------
# Whole-message validation
# --------------------------------------------------------------------------

def usable_request_id(message: dict) -> Optional[str]:
    """The request_id to correlate an ERROR with, if the message has a valid one."""
    rid = message.get("request_id")
    return rid if is_uuid(rid) else None


def validate_message(message: dict, *, transport: str, from_server: bool,
                     strict: Optional[bool] = None) -> MessageSpec:
    """Check a decoded message against the envelope rules and the catalogue.

    Inputs:
      message      dict returned by framing.decode_json
      transport    "TCP" or "UDP": the socket type it arrived on
      from_server  True when checking a message the server sent
      strict       reject unknown fields (default: True for client messages,
                   False for server messages, as the report specifies)
    Output: the MessageSpec of the message type.
    Raises ProtocolError (INVALID_MESSAGE, or UNSUPPORTED_VERSION which is fatal).
    """
    if strict is None:
        strict = not from_server
    reply_to = None if from_server else usable_request_id(message)

    def fail(detail: str, code: str = "INVALID_MESSAGE", fatal: bool = False):
        raise ProtocolError(code, detail, fatal=fatal, reply_to=reply_to)

    # 1. Version first: a different version may use a different layout.
    if "v" not in message:
        fail("missing field 'v'")
    if type(message["v"]) is not int:
        fail("field 'v' must be an integer")
    if message["v"] != PROTOCOL_VERSION:
        fail(f"unsupported protocol version {message['v']}", code="UNSUPPORTED_VERSION", fatal=True)

    # 2. Type, direction and transport.
    mtype = message.get("type")
    if not isinstance(mtype, str):
        fail("missing or non-string field 'type'")
    spec = CATALOGUE.get(mtype)
    if spec is None:
        fail(f"unknown message type {mtype!r}")
    if spec.from_client == from_server:
        fail(f"{mtype} is never sent by the {'server' if from_server else 'client'}")
    if spec.transport != transport:
        fail(f"{mtype} must be sent over {spec.transport}, not {transport}")

    data = message.get("data")
    if not isinstance(data, dict):
        fail("field 'data' must be an object")

    # 3. Envelope fields, then data fields.
    required, optional = _envelope_fields(spec)
    _check_fields(message, required, optional, {"v", "type", "data"}, strict, "envelope", fail)
    if spec.data is not None:
        _check_fields(data, spec.data, {}, set(), strict, "data", fail)
    elif mtype == "ERROR":
        _check_error_data(data, strict, fail)
    # RESULT data depends on the request (validate_result); STATE has validate_state.
    return spec


def _check_error_data(data: dict, strict: bool, fail) -> None:
    code = data.get("code")
    if code not in ERROR_CODES:
        fail(f"unknown error code {code!r}")
    required = {"code": "str", "message": "str", "retryable": "bool",
                **ERROR_REQUIRED_EXTRA.get(code, {})}
    _check_fields(data, required, ERROR_OPTIONAL_EXTRA.get(code, {}), set(), strict, "data", fail)
    if data["retryable"] and code not in RETRYABLE_ERROR_CODES:
        fail(f"{code} must have retryable=false (only RATE_LIMITED/SERVER_BUSY may be retried)")


def validate_result(data: dict, request_type: str, *, strict: bool = True) -> None:
    """Check RESULT data for the request it answers (identity, answer, UDP bind, ...)."""

    def fail(detail: str):
        raise ProtocolError("INVALID_MESSAGE", f"RESULT for {request_type}: {detail}")

    _check_fields(data, RESULT_DATA.get(request_type, {}), {}, set(), strict, "data", fail)


# --------------------------------------------------------------------------
# Quiz content rules (INVALID_QUIZ)
# --------------------------------------------------------------------------

def _quiz_error(detail: str):
    raise ProtocolError("INVALID_QUIZ", detail)


def _text_ok(text: str, limit: int) -> bool:
    # Nonempty after trimming; limits count Unicode code points (Python len()).
    return 1 <= len(text.strip()) <= limit


def validate_question(question: dict) -> None:
    """Content rules for one Question: 2..8 choices, 1-300 s timer, texts, IDs."""
    if not is_question_shape(question):
        raise ProtocolError("INVALID_MESSAGE", "question has the wrong fields or field types")
    if not _text_ok(question["text"], MAX_QUESTION_TEXT):
        _quiz_error(f"question text must be 1..{MAX_QUESTION_TEXT} characters after trimming")
    choices = question["choices"]
    if not MIN_CHOICES <= len(choices) <= MAX_CHOICES:
        _quiz_error(f"a question needs {MIN_CHOICES}..{MAX_CHOICES} choices, got {len(choices)}")
    ids = [c["choice_id"] for c in choices]
    if len(set(ids)) != len(ids):
        _quiz_error("choice IDs must be unique")
    if question["correct_choice_id"] not in ids:
        _quiz_error("correct_choice_id must be one of the question's choices")
    for choice in choices:
        if not _text_ok(choice["text"], MAX_CHOICE_TEXT):
            _quiz_error(f"choice text must be 1..{MAX_CHOICE_TEXT} characters after trimming")
    if not MIN_TIME_MS <= question["time_limit_ms"] <= MAX_TIME_MS:
        _quiz_error(f"time_limit_ms must be {MIN_TIME_MS}..{MAX_TIME_MS}")


def validate_quiz(title: str, questions: list) -> None:
    """Content rules for a whole quiz: title, 1..30 questions, unique IDs, 512 KiB."""
    if not isinstance(title, str) or not _text_ok(title, MAX_TITLE):
        _quiz_error(f"title must be 1..{MAX_TITLE} characters after trimming")
    if not isinstance(questions, list) or not MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS:
        count = len(questions) if isinstance(questions, list) else "not a list"
        _quiz_error(f"a quiz needs {MIN_QUESTIONS}..{MAX_QUESTIONS} questions, got {count}")
    for question in questions:
        validate_question(question)
    ids = [q["question_id"] for q in questions]
    if len(set(ids)) != len(ids):
        _quiz_error("question IDs must be unique")
    if len(encode_json({"title": title, "questions": questions})) > MAX_QUIZ_BYTES:
        _quiz_error(f"quiz content exceeds {MAX_QUIZ_BYTES} bytes")


# --------------------------------------------------------------------------
# STATE snapshot rules (used by clients and tests to check the server)
# --------------------------------------------------------------------------

STATE_FIELDS = {
    "control_rev": "int",
    "phase": "enum:" + ",".join(PHASES),
    "finish_reason": "enum:" + ",".join(FINISH_REASONS) + "|null",
    "pause_reason": "enum:" + ",".join(PAUSE_REASONS) + "|null",
    "editing": "bool",
    "host_connected": "bool",
    "title": "str|null",
    "question_index": "int|null",
    "question_count": "int",
    "current_question": "object|null",
    "remaining_ms": "int",
    "self": "object",
    "leaderboard": "list",
    "last_result": "object|null",
    "live": "object|null",
}
HOST_STATE_FIELDS = {"quiz": "object|null", "participants": "list"}


def validate_state(message: dict, *, role: str, strict: bool = True) -> None:
    """Check a STATE message's data against the snapshot rules of section 2.3.

    role is the receiving client's role ("HOST" or "PLAYER"): the snapshot is
    filtered per recipient, and a player must never receive question text,
    answer text, the correct choice before close, or host management data.
    """

    def fail(detail: str):
        raise ProtocolError("INVALID_MESSAGE", f"STATE: {detail}")

    data = message.get("data")
    if not isinstance(data, dict):
        fail("data must be an object")
    host = role == "HOST"
    _check_fields(data, {**STATE_FIELDS, **(HOST_STATE_FIELDS if host else {})}, {}, set(),
                  strict, "data", fail)
    phase = data["phase"]

    # Cross-field rules.
    if (data["finish_reason"] is not None) != (phase == "FINISHED"):
        fail("finish_reason must be set exactly when phase is FINISHED")
    if (data["pause_reason"] is not None) != (phase == "PAUSED"):
        fail("pause_reason must be set exactly when phase is PAUSED")
    if data["editing"] != (phase == "PAUSED" and data["pause_reason"] == "EDITING"):
        fail("editing must be true exactly when PAUSED with reason EDITING")
    if phase not in ("OPEN", "PAUSED") and data["remaining_ms"] != 0:
        fail("remaining_ms must be 0 when no question is live")
    if phase == "LOBBY" and (data["current_question"] is not None or data["question_index"] is not None):
        fail("LOBBY has no current question")

    # self
    me = data["self"]
    _check_fields(me, {"actor_id": "uuid", "role": "enum:HOST,PLAYER", "username": "str",
                       "total_score": "int|null", "submitted_choice_id": "uuid|null"},
                  {}, set(), strict, "self", fail)
    if me["role"] != role:
        fail(f"self.role is {me['role']}, expected {role}")
    if host and (me["total_score"] is not None or me["submitted_choice_id"] is not None):
        fail("host self has no score or submitted choice")
    if not host and me["total_score"] is None:
        fail("player self.total_score must be an integer")

    # current_question: players get IDs and selection symbols only.
    question = data["current_question"]
    if question is not None:
        choice_fields = {"choice_id": "uuid", "symbol_index": "int"}
        q_fields = {"question_id": "uuid", "attempt_id": "uuid", "choices": "list", "time_limit_ms": "int"}
        if host:
            q_fields["text"] = "str"
            choice_fields["text"] = "str"
        _check_fields(question, q_fields, {}, set(), True, "current_question", fail)
        for position, choice in enumerate(question["choices"]):
            if not isinstance(choice, dict):
                fail("each choice must be an object")
            _check_fields(choice, choice_fields, {}, set(), True, "choice", fail)
            if choice["symbol_index"] != position or position > 7:
                fail("symbol_index must equal the choice position 0..7")

    # leaderboard: dense ranks from 1, scores strictly descending, ties grouped.
    expected_rank, last_score = 1, None
    for row in data["leaderboard"]:
        if not isinstance(row, dict):
            fail("leaderboard rows must be objects")
        _check_fields(row, {"rank": "int", "score": "int", "players": "list"}, {}, set(), strict,
                      "leaderboard row", fail)
        if row["rank"] != expected_rank or (last_score is not None and row["score"] >= last_score):
            fail("leaderboard must use dense ranks 1,2,3... with strictly descending scores")
        for player in row["players"]:
            if not isinstance(player, dict):
                fail("leaderboard players must be objects")
            _check_fields(player, {"player_id": "uuid", "username": "str",
                                   "participation": "enum:" + ",".join(PARTICIPATION)},
                          {}, set(), strict, "leaderboard player", fail)
        expected_rank, last_score = expected_rank + 1, row["score"]

    # last_result: correctness appears only for a closed attempt.
    result = data["last_result"]
    if result is not None:
        _check_fields(result, {"question_id": "uuid", "attempt_id": "uuid", "correct_choice_id": "uuid",
                               "reason": "enum:" + ",".join(CLOSE_REASONS),
                               "own_result": "object|null"}, {}, set(), strict, "last_result", fail)
        own = result["own_result"]
        if host and own is not None:
            fail("host last_result.own_result must be null")
        if not host:
            if own is None:
                fail("player last_result.own_result is required")
            _check_fields(own, {"choice_id": "uuid|null", "correct": "bool", "points": "int",
                                "total_score": "int"}, {}, set(), strict, "own_result", fail)
        if phase == "OPEN" and question is not None and result["attempt_id"] == question["attempt_id"]:
            fail("an open attempt's result must not be revealed")

    # live: optional recovery counts.
    live = data["live"]
    if live is not None:
        _check_fields(live, {"answered_count": "int", "eligible_count": "int"}, {}, set(), strict,
                      "live", fail)
        if live["answered_count"] > live["eligible_count"]:
            fail("answered_count cannot exceed eligible_count")
