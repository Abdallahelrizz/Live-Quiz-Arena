"""One TCP connection of the server: ordered sending through a writer thread.

Why a queue and a writer thread?

  * Room code holds the room lock while it decides what to send. Writing to
    a socket can block for a long time if the client is slow, and that would
    freeze the whole room. send() only puts the frame in a queue, so it
    returns immediately and is safe to call while a lock is held.
  * Several threads send to the same client (its reader thread, other
    players' reader threads, the room timer). Only the writer thread ever
    calls sendall(), so two frames can never be mixed together on the wire.
  * The queue keeps the order in which the room changed, so a client always
    sees RESULT before the STATE that follows it.

A client that stops reading fills its queue; it is then disconnected rather
than allowed to hold memory or delay anyone else.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Optional, Tuple

from fatrat.common import log, new_id
from fatrat.protocol.catalogue import TCP
from fatrat.protocol.framing import encode_frame
from fatrat.protocol.messages import validate_message

MAX_QUEUED_FRAMES = 1000
PING_INTERVAL = 5.0       # seconds of quiet after the last PONG before the next PING
PONG_TIMEOUT = 15.0       # seconds allowed to answer a PING
REQUESTS_PER_SECOND = 10  # sustained request rate allowed per connection
REQUEST_BURST = 20        # requests allowed at once after a quiet moment


def error(code: str, text: str, reply_to: Optional[str], room_id: Optional[str] = None,
          retryable: bool = False, **extra) -> dict:
    """Build an ERROR message.

    Inputs: error code from the catalogue, a human-readable message, the
    request_id being answered (None if the request had no usable one), the
    room if known, whether simply trying again later can work (only
    RATE_LIMITED), and any extra data fields that code requires.
    """
    message = {"v": 1, "type": "ERROR", "reply_to": reply_to,
               "data": {"code": code, "message": text[:200], "retryable": retryable, **extra}}
    if room_id:
        message["room_id"] = room_id
    return message


def result(reply_to: str, room_id: str, data: dict) -> dict:
    """Build a successful RESULT for the request `reply_to`."""
    return {"v": 1, "type": "RESULT", "reply_to": reply_to, "room_id": room_id, "data": data}


class Connection:
    """A connected TCP client.

    member is the room Member this connection is attached to, or None before
    CREATE_ROOM / JOIN_ROOM / RESUME_SESSION succeeds. It is read and written
    only by this connection's reader thread, or under the member's room lock.
    """

    _CLOSE = object()   # marker in the queue: close after everything before it is written

    def __init__(self, sock: socket.socket, peer: Tuple[str, int]):
        self.sock = sock
        self.peer = peer
        self.who = f"{peer[0]}:{peer[1]}"
        self.member = None
        self._queue: "queue.Queue" = queue.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._closed = threading.Event()
        # Heartbeat and rate limit. Touched by this connection's reader thread
        # and by the server's heartbeat thread, so they have their own lock.
        self._meter = threading.Lock()
        self._ping_id: Optional[str] = None
        self._ping_sent = 0.0
        self._last_alive = time.monotonic()
        self._tokens = float(REQUEST_BURST)
        self._tokens_at = time.monotonic()
        threading.Thread(target=self._write_loop, name=f"writer {self.who}", daemon=True).start()

    def send(self, message: dict) -> None:
        """Queue one message. Never blocks, so it may be called under a lock."""
        # Self-check: the server never puts a message on the wire that breaks its own protocol.
        validate_message(message, transport=TCP, from_server=True, strict=True)
        self._enqueue(encode_frame(message))

    def close(self) -> None:
        """Close the connection after everything already queued has been written."""
        self._enqueue(self._CLOSE)

    # -- heartbeat (report section 2.7) ------------------------------------
    #
    # TCP does not report a client that silently vanished (Wi-Fi off, laptop
    # lid closed): with nothing to send, the connection just looks idle. The
    # server therefore asks "are you there?" with PING and expects PONG.

    def heartbeat(self, now: float, interval: float = PING_INTERVAL, timeout: float = PONG_TIMEOUT) -> bool:
        """One check by the heartbeat thread. Returns False if the client is considered gone."""
        with self._meter:
            if self._ping_id is not None:
                return now - self._ping_sent <= timeout
            if now - self._last_alive < interval:
                return True
            self._ping_id, self._ping_sent = new_id(), now
            ping_id = self._ping_id
        self.send({"v": 1, "type": "PING", "data": {"ping_id": ping_id}})
        return True

    def pong(self, ping_id: str) -> None:
        """A PONG arrived. Only the answer to the outstanding PING counts."""
        with self._meter:
            if ping_id == self._ping_id:
                self._ping_id, self._last_alive = None, time.monotonic()

    # -- rate limit (report section 2.4) -------------------------------------

    def allow_request(self) -> int:
        """Token bucket. Returns 0 if the request may run, else how many ms to wait.

        Every request spends one token; tokens come back at 10 per second up
        to 20. A normal client never notices; a client flooding the server
        gets RATE_LIMITED instead of taking time from everyone else.
        """
        now = time.monotonic()
        with self._meter:
            self._tokens = min(float(REQUEST_BURST),
                               self._tokens + (now - self._tokens_at) * REQUESTS_PER_SECOND)
            self._tokens_at = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0
            return int((1 - self._tokens) * 1000 / REQUESTS_PER_SECOND) + 1

    def _enqueue(self, item) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log(self.who, "writer queue full: disconnecting slow client")
            self.shutdown()

    def _write_loop(self) -> None:
        """Writer thread: the only place that writes to this socket."""
        while not self._closed.is_set():
            item = self._queue.get()
            if item is self._CLOSE:
                break
            try:
                self.sock.sendall(item)      # sendall() loops until every byte is written
            except OSError:
                break                        # client vanished; the reader thread sees it too
        self.shutdown()

    def shutdown(self) -> None:
        """Close the socket now. Also wakes the reader thread, which then cleans up."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        try:
            self._queue.put_nowait(self._CLOSE)   # release the writer thread if it is waiting
        except queue.Full:
            pass
