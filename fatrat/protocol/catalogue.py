"""The Fat-Rat message catalogue: the 32 message types of report section 2.3.

This file is data only. For each message type it records who sends it, who
receives it, which transport carries it, which envelope fields it uses and
which `data` fields it must contain. messages.py validates real messages
against it, and tests/test_report_consistency.py compares it with the Word
report, so the code and the report cannot silently disagree.

Sender/receiver letters follow the report: H host, P player, C either
client, S server.

Data field types (checked in messages.py):
    int          nonnegative integer <= 2**63 - 1 (booleans are not integers)
    str          JSON string
    uuid         canonical lowercase UUIDv4 string
    token        43-character unpadded URL-safe Base64 of 32 bytes
    nonce        32 lowercase hexadecimal characters
    question     Question object (shape only; content rules give INVALID_QUIZ)
    question_list / uuid_list   arrays of those
    enum:A,B     one of the listed strings
    <type>|null  that type or JSON null
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

PROTOCOL_VERSION = 1
TCP = "TCP"
UDP = "UDP"

# Envelope kinds: which top-level fields a message carries besides v/type/data
# (report table "Message kind / Additional envelope fields").
REQUEST = "request"            # client TCP request: request_id, plus room_id once attached
REPLY = "reply"                # RESULT / ERROR: reply_to, plus room_id when known
STATE_KIND = "state"           # STATE: room_id and state_rev
PING_KIND = "ping"             # PING / PONG: no request_id, reply_to or room_id
NOTICE = "notice"              # other TCP session notices: room_id when bound
UDP_HELLO_KIND = "udp_hello"   # session_id and binding_id
LIVE_STATUS_KIND = "live_status"  # room/session/binding/question/attempt IDs, control_rev, seq


@dataclass(frozen=True)
class MessageSpec:
    """One row of the message catalogue."""

    name: str
    sender: str
    receiver: str
    transport: str
    envelope: str
    # Required data fields -> field type. None means the data is checked by a
    # dedicated function (RESULT depends on the request, ERROR on its code,
    # STATE has its own structure).
    data: Optional[Dict[str, str]]

    @property
    def from_client(self) -> bool:
        return self.sender != "S"


_REV = {"expected_control_rev": "int"}


def _host(name: str, data: Dict[str, str]) -> MessageSpec:
    return MessageSpec(name, "H", "S", TCP, REQUEST, data)


MESSAGES = (
    # Identity and room membership
    MessageSpec("CREATE_ROOM", "H", "S", TCP, REQUEST, {"username": "str", "host_token": "token"}),
    MessageSpec("JOIN_ROOM", "P", "S", TCP, REQUEST, {"pin": "str", "username": "str"}),
    MessageSpec("RESUME_SESSION", "C", "S", TCP, REQUEST, {"room_id": "uuid", "reconnect_token": "token"}),
    _host("CLOSE_ROOM", _REV),
    _host("REMOVE_PLAYER", {**_REV, "player_id": "uuid"}),
    MessageSpec("LEAVE_ROOM", "P", "S", TCP, REQUEST, {}),
    MessageSpec("GET_STATE", "C", "S", TCP, REQUEST, {}),
    # Quiz and question control
    _host("PUT_QUIZ", {**_REV, "title": "str", "questions": "question_list"}),
    _host("START_QUIZ", _REV),
    MessageSpec("SUBMIT_ANSWER", "P", "S", TCP, REQUEST,
                {"question_id": "uuid", "attempt_id": "uuid", "choice_id": "uuid"}),
    _host("PAUSE_QUIZ", _REV),
    _host("RESUME_QUIZ", _REV),
    _host("CLOSE_QUESTION", _REV),
    _host("NEXT_QUESTION", _REV),
    _host("EDIT_BEGIN", {**_REV, "question_id": "uuid"}),
    _host("EDIT_COMMIT", {**_REV, "question": "question"}),
    _host("EDIT_CANCEL", _REV),
    _host("EDIT_QUESTION", {**_REV, "question": "question"}),
    _host("ADD_QUESTION", {**_REV, "question": "question", "before_question_id": "uuid|null"}),
    _host("DELETE_QUESTION", {**_REV, "question_id": "uuid"}),
    _host("REORDER_QUESTIONS", {**_REV, "question_ids": "uuid_list"}),
    # UDP registration, live status, replies and session events
    MessageSpec("REQUEST_UDP_BIND", "C", "S", TCP, REQUEST, {}),
    MessageSpec("UDP_HELLO", "C", "S", UDP, UDP_HELLO_KIND, {"nonce": "nonce"}),
    MessageSpec("UDP_BOUND", "S", "C", TCP, NOTICE, {"binding_id": "uuid"}),
    MessageSpec("LIVE_STATUS", "S", "C", UDP, LIVE_STATUS_KIND,
                {"remaining_ms": "int", "answered_count": "int", "eligible_count": "int"}),
    MessageSpec("RESULT", "S", "C", TCP, REPLY, None),
    MessageSpec("ERROR", "S", "C", TCP, REPLY, None),
    MessageSpec("STATE", "S", "C", TCP, STATE_KIND, None),
    MessageSpec("PING", "S", "C", TCP, PING_KIND, {"ping_id": "uuid"}),
    MessageSpec("PONG", "C", "S", TCP, PING_KIND, {"ping_id": "uuid"}),
    MessageSpec("REMOVED", "S", "P", TCP, NOTICE, {"reason": "enum:HOST_REMOVED", "message": "str"}),
    MessageSpec("SESSION_REPLACED", "S", "C", TCP, NOTICE, {"reason": "enum:RECONNECTED_ELSEWHERE"}),
)

CATALOGUE: Dict[str, MessageSpec] = {spec.name: spec for spec in MESSAGES}

# Requests sent before the connection is attached to a room: no envelope room_id.
UNATTACHED_REQUESTS = frozenset({"CREATE_ROOM", "JOIN_ROOM", "RESUME_SESSION"})

# RESULT data for each request type (report: RESULT row of the catalogue).
IDENTITY_RESULT = {
    "room_id": "uuid", "pin": "pin", "session_id": "uuid", "actor_id": "uuid",
    "role": "enum:HOST,PLAYER", "reconnect_token": "token",
}
RESULT_DATA: Dict[str, Dict[str, str]] = {
    "CREATE_ROOM": IDENTITY_RESULT,
    "JOIN_ROOM": IDENTITY_RESULT,
    "RESUME_SESSION": IDENTITY_RESULT,
    "SUBMIT_ANSWER": {"accepted": "true", "question_id": "uuid", "attempt_id": "uuid", "choice_id": "uuid"},
    "REQUEST_UDP_BIND": {"binding_id": "uuid", "udp_nonce": "nonce", "server_udp_port": "port"},
    "LEAVE_ROOM": {"left": "true"},
}  # every other request: generic success RESULT with data {}

# Error codes (report error table).
ERROR_CODES = frozenset({
    "INVALID_PIN", "INVALID_USERNAME", "NAME_TAKEN",
    "INVALID_MESSAGE", "UNSUPPORTED_VERSION",
    "INVALID_QUIZ", "INVALID_CHOICE",
    "NOT_AUTHENTICATED", "NOT_HOST", "NOT_PLAYER",
    "ALREADY_ATTACHED", "UNKNOWN_PLAYER",
    "ROOM_MISMATCH", "ROOM_FINISHED",
    "INVALID_TOKEN", "SESSION_REVOKED",
    "STALE_STATE", "STALE_ATTEMPT",
    "HOST_HAS_ROOM", "ROOM_ALREADY_CREATED",
    "ROOM_FULL", "NOT_ENOUGH_PLAYERS",
    "INVALID_STATE", "QUESTION_LOCKED",
    "QUESTION_LIMIT", "INVALID_ORDER",
    "ALREADY_ANSWERED",
    "QUESTION_PAUSED", "ANSWER_LATE",
    "REQUEST_ID_REUSED", "RATE_LIMITED", "SERVER_BUSY",
})
RETRYABLE_ERROR_CODES = frozenset({"RATE_LIMITED", "SERVER_BUSY"})
# Extra ERROR data fields that some codes must carry / may carry.
ERROR_REQUIRED_EXTRA: Dict[str, Dict[str, str]] = {
    "STALE_STATE": {"current_control_rev": "int"},
    "SESSION_REVOKED": {"reason": "enum:HOST_REMOVED,LEFT_ROOM"},
    "ALREADY_ANSWERED": {"question_id": "uuid", "attempt_id": "uuid", "choice_id": "uuid"},
    "HOST_HAS_ROOM": {"existing_room_id": "uuid"},
    "ROOM_ALREADY_CREATED": {"existing_room_id": "uuid"},
}
ERROR_OPTIONAL_EXTRA: Dict[str, Dict[str, str]] = {
    "RATE_LIMITED": {"retry_after_ms": "int"},
    "SERVER_BUSY": {"retry_after_ms": "int"},
}

# Room phases and other enumerations used in STATE.
PHASES = ("LOBBY", "OPEN", "PAUSED", "RESULTS", "FINISHED")
FINISH_REASONS = ("COMPLETED", "HOST_CLOSED")
PAUSE_REASONS = ("MANUAL", "EDITING", "HOST_DISCONNECTED")
CLOSE_REASONS = ("ALL_ANSWERED", "TIMEOUT", "HOST_CLOSED")
PARTICIPATION = ("ACTIVE", "LEFT", "REMOVED")

# Quiz limits (report section 2.2, approved team limits).
MIN_CHOICES, MAX_CHOICES = 2, 8
MIN_QUESTIONS, MAX_QUESTIONS = 1, 30
MIN_TIME_MS, MAX_TIME_MS = 1_000, 300_000
MAX_QUESTION_TEXT, MAX_CHOICE_TEXT, MAX_TITLE = 1000, 300, 100
MAX_QUIZ_BYTES = 524_288
MAX_NAME = 32
