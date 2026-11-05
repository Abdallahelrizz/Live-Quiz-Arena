"""TCP message framing for the Fat-Rat protocol (report section 2.2).

TCP delivers a byte stream, not messages. One recv() can return half a
message, or several messages glued together. Fat-Rat therefore puts a
4-byte header in front of every message:

    uint32_be(N) || N bytes of UTF-8 JSON

N counts only the JSON bytes (not the header) and must be 1..1,048,576.
The receiver uses an exact-read loop: read exactly 4 header bytes, then
exactly N body bytes, looping over recv() until each part is complete.
Because it never reads past the end of the current message, the next
message's bytes stay in the kernel buffer for the next call, so partial
reads and coalesced messages are both handled without losing boundaries.

A frame that has started arriving must finish within 10 seconds of its
first byte; waiting *between* frames is not limited here (idle detection
is the heartbeat's job).
"""

from __future__ import annotations

import json
import math
import socket
import struct
import threading
import time
from typing import Any, Optional

HEADER = struct.Struct("!I")          # "!" = network (big-endian) byte order, "I" = uint32
MAX_FRAME = 1_048_576                 # 1 MiB, largest allowed JSON body
PARTIAL_FRAME_TIMEOUT = 10.0          # seconds from a frame's first byte to its last


class FramingError(Exception):
    """The byte stream broke a framing rule. The connection must be closed.

    `reply_allowed` says whether a short ERROR may be sent before closing.
    After an invalid length the stream position is unknown, so the report
    allows closing without a reply.
    """

    reply_allowed = True


class FrameLengthError(FramingError):
    """Header announced N = 0 or N > MAX_FRAME."""

    reply_allowed = False

    def __init__(self, length: int):
        super().__init__(f"invalid frame length {length} (allowed 1..{MAX_FRAME})")
        self.length = length


class FrameTimeoutError(FramingError):
    """A started frame did not complete within the partial-frame timeout."""


class PayloadError(FramingError):
    """The frame body is not a valid protocol JSON object.

    Covers invalid UTF-8, invalid JSON, duplicate keys, NaN/Infinity and a
    top-level value that is not an object. All are fatal (section 2.2).
    """


class ConnectionClosed(Exception):
    """The peer closed the connection (EOF).

    `mid_frame` is True when EOF arrived after part of a frame. Either way
    the report treats EOF as a disconnect, not as a message.
    """

    def __init__(self, mid_frame: bool):
        super().__init__("connection closed mid-frame" if mid_frame else "connection closed")
        self.mid_frame = mid_frame


# --------------------------------------------------------------------------
# JSON encoding and strict decoding
# --------------------------------------------------------------------------

def encode_json(message: Any) -> bytes:
    """Encode a message as compact UTF-8 JSON (no ASCII escaping, no NaN)."""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8")


def _reject_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PayloadError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _reject_constant(name: str):
    # Called by json for the non-standard literals NaN, Infinity, -Infinity.
    raise PayloadError(f"non-finite number {name}")


def _parse_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):          # e.g. 1e999 overflows to infinity
        raise PayloadError(f"non-finite number {text}")
    return value


def decode_json(payload: bytes) -> dict:
    """Decode a frame body (or UDP datagram) into a JSON object.

    Input: raw bytes. Output: dict. Raises PayloadError for invalid UTF-8,
    invalid JSON, duplicate keys, non-finite numbers or a non-object.
    """
    try:
        text = payload.decode("utf-8")    # strict: invalid byte sequences raise
    except UnicodeDecodeError as exc:
        raise PayloadError(f"invalid UTF-8: {exc}") from exc
    try:
        obj = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except PayloadError:
        raise
    except ValueError as exc:             # json.JSONDecodeError is a ValueError
        raise PayloadError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise PayloadError("payload must be a JSON object")
    return obj


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def encode_frame(message: Any) -> bytes:
    """Return header + JSON body for one message, ready for sendall()."""
    body = encode_json(message)
    if not 1 <= len(body) <= MAX_FRAME:
        raise ValueError(f"message is {len(body)} bytes; frames allow 1..{MAX_FRAME}")
    return HEADER.pack(len(body)) + body


def send_message(sock: socket.socket, message: Any, lock: Optional[threading.Lock] = None) -> None:
    """Send one framed message with sendall().

    sendall() loops until every byte is written. If several threads write to
    the same socket, pass the connection's lock so frames never interleave.
    """
    frame = encode_frame(message)
    if lock is None:
        sock.sendall(frame)
    else:
        with lock:
            sock.sendall(frame)


# --------------------------------------------------------------------------
# Receiving: the exact-read loop
# --------------------------------------------------------------------------

def _recv_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    """Read exactly `count` bytes before `deadline` (time.monotonic()).

    recv() may return fewer bytes than asked for, so keep asking for the
    remainder. Raises FrameTimeoutError or ConnectionClosed(mid_frame=True).
    """
    chunks = []
    received = 0
    while received < count:
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            raise FrameTimeoutError("partial frame not completed within the time limit")
        sock.settimeout(remaining_time)
        try:
            chunk = sock.recv(min(count - received, 65536))
        except socket.timeout as exc:
            raise FrameTimeoutError("partial frame not completed within the time limit") from exc
        if not chunk:
            raise ConnectionClosed(mid_frame=True)
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket, partial_timeout: float = PARTIAL_FRAME_TIMEOUT) -> bytes:
    """Receive one complete frame body (the JSON bytes, header removed).

    Waiting for the *first* byte uses the socket's own timeout (None = wait
    forever), so an idle connection is not an error here. Once the first
    byte arrives, the whole frame must finish within `partial_timeout`.

    Raises ConnectionClosed, FrameLengthError or FrameTimeoutError.
    socket.timeout propagates only if the caller set an idle timeout.
    """
    idle_timeout = sock.gettimeout()
    try:
        first = sock.recv(HEADER.size)    # 1..4 bytes, or b"" at EOF
        if not first:
            raise ConnectionClosed(mid_frame=False)
        deadline = time.monotonic() + partial_timeout
        header = first + _recv_exact(sock, HEADER.size - len(first), deadline)
        (length,) = HEADER.unpack(header)
        if not 1 <= length <= MAX_FRAME:
            raise FrameLengthError(length)
        return _recv_exact(sock, length, deadline)
    finally:
        sock.settimeout(idle_timeout)


def recv_message(sock: socket.socket, partial_timeout: float = PARTIAL_FRAME_TIMEOUT) -> dict:
    """Receive one frame and decode it as a JSON object (see decode_json)."""
    return decode_json(recv_frame(sock, partial_timeout))
