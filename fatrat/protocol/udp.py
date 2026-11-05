"""UDP datagrams and the LIVE_STATUS acceptance filter (report sections 2.2 and 2.6).

A UDP datagram carries exactly one JSON object with no length prefix; the
datagram itself is the message boundary. Payloads are limited to 512 bytes.
Oversized, truncated, malformed or unexpected datagrams are dropped silently.

UDP can lose, duplicate and reorder datagrams. LIVE_STATUS snapshots are
replaceable: each one fully describes the countdown and answered count, so
a lost snapshot is simply superseded by the next one and is never
retransmitted. The client accepts a snapshot only if (section 2.6 step 5):

  1. it came from the server's UDP address,
  2. its binding was confirmed over TCP (UDP_BOUND),
  3. room, session, question, attempt and control_rev match the last TCP STATE,
  4. its seq is strictly greater than the last accepted seq for this binding.

Rule 4 rejects duplicates and late (reordered) snapshots, so the countdown
never jumps backwards. UDP never changes phase, answers or scores.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

from .catalogue import UDP
from .framing import PayloadError, decode_json, encode_json
from .messages import ProtocolError, validate_message

MAX_DATAGRAM = 512
# recvfrom() buffer: larger than any real datagram, so an oversized payload
# is received whole and can be detected, instead of being silently truncated.
RECV_BUFFER = 65535

# Verdicts returned by LiveStatusFilter.check()
ACCEPTED = "accepted"
OVERSIZE = "oversize (>512 bytes)"
MALFORMED = "malformed JSON or fields"
UNEXPECTED = "unexpected message type"
WRONG_SOURCE = "wrong source address"
NOT_BOUND = "binding not confirmed yet"
WRONG_BINDING = "wrong binding_id"
NO_LIVE_QUESTION = "no live question in last STATE"
WRONG_SESSION = "wrong room/session"
WRONG_ATTEMPT = "wrong question/attempt"
FUTURE_REVISION = "future control_rev"
OLD_REVISION = "old control_rev"
DUPLICATE = "duplicate seq"
OUT_OF_ORDER = "older seq (out of order)"


def encode_datagram(message: dict) -> bytes:
    """Encode one message as a datagram payload; refuses anything over 512 bytes."""
    payload = encode_json(message)
    if len(payload) > MAX_DATAGRAM:
        raise ValueError(f"datagram is {len(payload)} bytes; the limit is {MAX_DATAGRAM}")
    return payload


def parse_datagram(payload: bytes, *, from_server: bool) -> Tuple[Optional[dict], str]:
    """Decode and validate one datagram.

    Returns (message, ACCEPTED) or (None, reason) when it must be dropped.
    """
    if len(payload) > MAX_DATAGRAM:
        return None, OVERSIZE
    try:
        message = decode_json(payload)
        validate_message(message, transport=UDP, from_server=from_server)
    except (PayloadError, ProtocolError):
        return None, MALFORMED
    return message, ACCEPTED


class LiveStatusFilter:
    """Client-side acceptance rules for LIVE_STATUS (thread-safe).

    The TCP reader thread calls confirm_binding() and set_context(); the UDP
    receiver thread calls check(). A lock keeps the two consistent.
    """

    def __init__(self, server_udp_addr: Tuple[str, int]):
        self._lock = threading.Lock()
        self.server_udp_addr = server_udp_addr
        self.binding_id: Optional[str] = None
        self.context: Optional[dict] = None
        self.last_seq = 0

    def confirm_binding(self, binding_id: str) -> None:
        """UDP_BOUND arrived over TCP: accept this binding, restart seq at 0."""
        with self._lock:
            self.binding_id = binding_id
            self.last_seq = 0

    def set_context(self, *, room_id: str, session_id: str, question_id: str,
                    attempt_id: str, control_rev: int) -> None:
        """Latest TCP STATE has a live question: snapshots must match it."""
        with self._lock:
            self.context = {"room_id": room_id, "session_id": session_id, "question_id": question_id,
                            "attempt_id": attempt_id, "control_rev": control_rev}

    def clear_context(self) -> None:
        """Latest TCP STATE has no live question: accept no snapshots."""
        with self._lock:
            self.context = None

    def check(self, payload: bytes, source: Tuple[str, int]) -> Tuple[Optional[dict], str]:
        """Decide whether to use one received datagram.

        Inputs: raw payload and the (ip, port) it came from.
        Output: (message, ACCEPTED) or (None or message, reason it was rejected).
        """
        message, verdict = parse_datagram(payload, from_server=True)
        if message is None:
            return None, verdict
        if message["type"] != "LIVE_STATUS":
            return message, UNEXPECTED
        with self._lock:
            if tuple(source) != tuple(self.server_udp_addr):
                return message, WRONG_SOURCE
            if self.binding_id is None:
                return message, NOT_BOUND
            if message["binding_id"] != self.binding_id:
                return message, WRONG_BINDING
            ctx = self.context
            if ctx is None:
                return message, NO_LIVE_QUESTION
            if message["room_id"] != ctx["room_id"] or message["session_id"] != ctx["session_id"]:
                return message, WRONG_SESSION
            if message["question_id"] != ctx["question_id"] or message["attempt_id"] != ctx["attempt_id"]:
                return message, WRONG_ATTEMPT
            if message["control_rev"] > ctx["control_rev"]:
                return message, FUTURE_REVISION
            if message["control_rev"] < ctx["control_rev"]:
                return message, OLD_REVISION
            if message["seq"] == self.last_seq:
                return message, DUPLICATE
            if message["seq"] < self.last_seq:
                return message, OUT_OF_ORDER
            self.last_seq = message["seq"]
            return message, ACCEPTED
