"""ClientSession: everything a Fat-Rat client does on the network.

This module has no interface code. The terminal clients, the bots and the
graphical interface all use the same ClientSession and differ only in how
they show what it reports. It reports through one callback:

    on_event(kind, payload)

    "state"       payload = the STATE data dict (the whole room as this client may see it)
    "live"        payload = {"remaining_ms", "answered_count", "eligible_count"} from UDP
    "connection"  payload = "connected" | "lost" | "reconnecting" | "resumed" | "closed"
    "notice"      payload = {"type": "REMOVED" | "SESSION_REPLACED" | "REVOKED", ...}

The callback runs on the session's own threads, so an interface must hand
the event to its own thread before touching its widgets.

Threads of one client
---------------------
  caller's thread   sends requests and waits for their replies (request())
  TCP reader        receives every frame; answers PING; stores STATE;
                    wakes the caller waiting for a RESULT or ERROR
  UDP receiver      receives LIVE_STATUS; asks for a TCP refresh if UDP goes quiet
  reconnect thread  only after the connection is lost: tries to resume

Shared data (pending replies, latest state, countdown sample) is protected
by one lock. A separate send lock makes sure two threads never write to the
TCP socket at the same time, which would mix two frames together.

TCP and UDP on the client
-------------------------
TCP carries every request and every STATE. UDP only carries the countdown
and the answered count. A UDP snapshot is used only if LiveStatusFilter
accepts it: right sender, confirmed binding, matching the question in the
last STATE, and newer than the last one. So a lost, repeated or late
datagram can never move the countdown backwards or change what is shown as
the result; the result always comes from TCP.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from fatrat.common import TCP_PORT, new_id
from fatrat.protocol import udp
from fatrat.protocol.catalogue import TCP, UNATTACHED_REQUESTS
from fatrat.protocol.framing import ConnectionClosed, FramingError, encode_frame, recv_message
from fatrat.protocol.messages import ProtocolError, validate_message

REQUEST_TIMEOUT = 8.0            # seconds to wait for a RESULT or ERROR
UDP_QUIET_SECONDS = 3.0          # without a snapshot for this long, the countdown is "stale"
UDP_REFRESH_SECONDS = 5.0        # at most one GET_STATE per this long while UDP is quiet
RECONNECT_DELAYS = (1, 2, 4, 8, 15)   # seconds; the last value repeats


class RequestFailed(Exception):
    """The server answered a request with ERROR.

    code is the protocol error code (NAME_TAKEN, ROOM_FULL, ...), message is
    the human-readable text, data is the whole ERROR data object.
    """

    def __init__(self, data: dict):
        super().__init__(f"{data.get('code')}: {data.get('message')}")
        self.code, self.message, self.data = data.get("code"), data.get("message"), data


class NotConnected(Exception):
    """There is no working connection, or no reply arrived in time."""


class ClientSession:
    def __init__(self, server_ip: str, tcp_port: int = TCP_PORT,
                 on_event: Optional[Callable[[str, object], None]] = None,
                 auto_reconnect: bool = True):
        self.server_ip, self.tcp_port = server_ip, tcp_port
        self.on_event = on_event or (lambda kind, payload: None)
        self.auto_reconnect = auto_reconnect

        self._lock = threading.Lock()          # pending, state, countdown sample
        self._send_lock = threading.Lock()     # one writer at a time on the TCP socket
        self._sock: Optional[socket.socket] = None
        self._pending = {}                     # request_id -> [Event, reply or None]
        self._closing = False
        self._have_state = threading.Event()   # set once a STATE has arrived for the current attachment

        self.identity: Optional[dict] = None   # room_id, pin, session_id, actor_id, role, reconnect_token
        self.state: Optional[dict] = None      # latest STATE data
        self._state_rev = -1
        self._sample = None                    # (remaining_ms, time.monotonic(), running)
        self.live: Optional[dict] = None       # latest accepted LIVE_STATUS data

        self._udp_sock: Optional[socket.socket] = None
        self._filter: Optional[udp.LiveStatusFilter] = None
        self._last_snapshot = 0.0
        self._last_refresh = 0.0
        self.udp_bound = False
        self.udp_dropped = {}                  # reason -> count, for tests and the demo

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the TCP connection and start the reader thread. Raises OSError if unreachable."""
        sock = socket.create_connection((self.server_ip, self.tcp_port), timeout=5)
        sock.settimeout(None)                                   # the reader blocks until data arrives
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._lock:
            self._sock = sock
        threading.Thread(target=self._read_loop, args=(sock,), name="tcp reader", daemon=True).start()
        self.on_event("connection", "connected")

    def close(self) -> None:
        """Close both sockets. This is a plain disconnect, not LEAVE_ROOM: the identity is kept."""
        self._closing = True
        with self._lock:
            sock, self._sock = self._sock, None
        for s in (sock, self._udp_sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._fail_pending()
        self.on_event("connection", "closed")

    def new_connection(self) -> None:
        """Leave the current room's connection and open a fresh, unattached one.

        A TCP connection belongs to one room for its whole life (the server
        answers ALREADY_ATTACHED otherwise). The host needs this when the
        room it returned to has already finished and it must create a new
        one. The old reader thread sees that it is no longer the current
        connection and ends without reconnecting.
        """
        with self._lock:
            sock, self._sock = self._sock, None
            self.identity, self.state, self._state_rev = None, None, -1
        self._have_state.clear()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._fail_pending()
        self.udp_bound = False
        self.connect()

    def _send(self, message: dict) -> None:
        with self._lock:
            sock = self._sock
        if sock is None:
            raise NotConnected("not connected")
        try:
            with self._send_lock:
                sock.sendall(encode_frame(message))             # 4-byte length + JSON, written whole
        except OSError as exc:
            raise NotConnected(str(exc)) from exc

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------

    def request(self, mtype: str, data: dict, timeout: float = REQUEST_TIMEOUT,
                request_id: Optional[str] = None, _retry: bool = True) -> dict:
        """Send one request and wait for its reply.

        Returns the RESULT data. Raises RequestFailed for an ERROR reply and
        NotConnected if the connection is down or no reply arrives in time.
        Every request has a fresh request_id; the reply carries the same ID
        in reply_to, which is how replies are matched to requests even when
        STATE messages arrive in between.
        """
        rid = request_id or new_id()
        message = {"v": 1, "type": mtype, "request_id": rid, "data": data}
        if mtype not in UNATTACHED_REQUESTS:
            if self.identity is None:
                raise NotConnected("not in a room")
            message["room_id"] = self.identity["room_id"]
        slot = [threading.Event(), None, mtype]
        with self._lock:
            self._pending[rid] = slot
        try:
            self._send(message)
            if not slot[0].wait(timeout) or slot[1] is None:
                raise NotConnected(f"no reply to {mtype}")
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        reply = slot[1]
        if reply["type"] == "ERROR":
            if reply["data"]["code"] == "RATE_LIMITED" and request_id is None and _retry:
                # The server did nothing with the request, so it is safe to wait and send it again.
                time.sleep(reply["data"].get("retry_after_ms", 200) / 1000)
                return self.request(mtype, data, timeout, _retry=False)
            raise RequestFailed(reply["data"])
        if mtype in UNATTACHED_REQUESTS:
            self._have_state.wait(3)        # the server sends STATE right after an identity RESULT
        return reply["data"]

    def create_room(self, host_name: str, host_token: str, request_id: Optional[str] = None) -> dict:
        return self.request("CREATE_ROOM", {"username": host_name, "host_token": host_token},
                            request_id=request_id)

    def join(self, pin: str, username: str) -> dict:
        return self.request("JOIN_ROOM", {"pin": pin, "username": username})

    def resume(self, room_id: str, reconnect_token: str) -> dict:
        return self.request("RESUME_SESSION", {"room_id": room_id, "reconnect_token": reconnect_token})

    def host_command(self, mtype: str, **data) -> dict:
        """A host command with the control_rev of the state the host is looking at.

        If the room changed in the meantime the server answers STALE_STATE
        instead of acting on an outdated decision.
        """
        before = self._state_rev
        reply = self.request(mtype, {"expected_control_rev": self.state["control_rev"], **data})
        # The server sends RESULT first and the new STATE right after it. Wait
        # briefly for that STATE, so the caller's next command carries the new
        # control_rev instead of being refused as stale.
        deadline = time.monotonic() + 0.5
        while self._state_rev == before and time.monotonic() < deadline:
            time.sleep(0.005)
        return reply

    def submit_answer(self, choice_id: str) -> dict:
        q = self.state["current_question"]
        return self.request("SUBMIT_ANSWER", {"question_id": q["question_id"],
                                              "attempt_id": q["attempt_id"], "choice_id": choice_id})

    # ------------------------------------------------------------------
    # TCP reader thread
    # ------------------------------------------------------------------

    def _read_loop(self, sock: socket.socket) -> None:
        while True:
            try:
                message = recv_message(sock)                    # exactly one frame, however TCP split it
                validate_message(message, transport=TCP, from_server=True)
            except ProtocolError:
                continue                                        # unknown server message: ignore it
            except (ConnectionClosed, FramingError, OSError):
                break                                           # broken stream: this connection is over
            try:
                self._on_message(message)
            except Exception:                                   # an interface bug must not kill the reader
                pass
        try:
            sock.close()
        except OSError:
            pass
        with self._lock:
            current = self._sock is sock
            if current:
                self._sock = None
        if not current:
            return                                              # a newer connection already replaced this one
        self._fail_pending()
        self.udp_bound = False
        if self._closing:
            return
        self.on_event("connection", "lost")
        if self.auto_reconnect and self.identity is not None:
            threading.Thread(target=self._reconnect_loop, name="reconnect", daemon=True).start()

    def _on_message(self, m: dict) -> None:
        mtype = m["type"]
        if mtype in ("RESULT", "ERROR"):
            with self._lock:
                slot = self._pending.get(m.get("reply_to"))
            if slot is not None:
                if mtype == "RESULT" and slot[2] in UNATTACHED_REQUESTS:
                    # Stored here, on the reader thread, so it is already in place
                    # when the STATE that follows this RESULT is processed.
                    self.identity = m["data"]
                    self._state_rev = -1                        # a new attachment starts a new STATE series
                    self._have_state.clear()
                slot[1] = m
                slot[0].set()
        elif mtype == "STATE":
            self._on_state(m)
        elif mtype == "PING":
            self._send({"v": 1, "type": "PONG", "data": {"ping_id": m["data"]["ping_id"]}})
        elif mtype == "UDP_BOUND":
            if self._filter is not None:
                self._filter.confirm_binding(m["data"]["binding_id"])
                self.udp_bound = True
                self._last_snapshot = time.monotonic()
        elif mtype in ("REMOVED", "SESSION_REPLACED"):
            self.auto_reconnect = False                         # do not fight for the session
            self.on_event("notice", {"type": mtype, **m["data"]})

    def _on_state(self, m: dict) -> None:
        data = m["data"]
        with self._lock:
            if m["state_rev"] < self._state_rev:
                return                                          # older than what we already show
            self._state_rev = m["state_rev"]
            self.state = data
            self._sample = (data["remaining_ms"], time.monotonic(), data["phase"] == "OPEN")
            if data["live"] is not None:
                self.live = {"remaining_ms": data["remaining_ms"], **data["live"]}
            elif data["phase"] not in ("OPEN", "PAUSED"):
                self.live = None
        q = data["current_question"]
        if self._filter is not None and self.identity is not None:
            if data["phase"] in ("OPEN", "PAUSED") and q is not None:
                self._filter.set_context(room_id=m["room_id"], session_id=self.identity["session_id"],
                                         question_id=q["question_id"], attempt_id=q["attempt_id"],
                                         control_rev=data["control_rev"])
            else:
                self._filter.clear_context()
        self._have_state.set()
        self.on_event("state", data)

    def _fail_pending(self) -> None:
        with self._lock:
            slots = list(self._pending.values())
        for slot in slots:
            slot[0].set()                                       # wakes request(); slot[1] stays None

    # ------------------------------------------------------------------
    # Reconnect
    # ------------------------------------------------------------------

    def _reconnect_loop(self) -> None:
        """Try to resume the same identity with the reconnect token, waiting longer each time."""
        attempt = 0
        while not self._closing and self.auto_reconnect:
            self.on_event("connection", "reconnecting")
            time.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            attempt += 1
            try:
                self.connect()
                self.resume(self.identity["room_id"], self.identity["reconnect_token"])
            except RequestFailed as exc:                        # INVALID_TOKEN or SESSION_REVOKED: give up
                self.auto_reconnect = False
                self.on_event("notice", {"type": "REVOKED", "code": exc.code, "message": exc.message,
                                         "reason": exc.data.get("reason")})
                self.close()
                return
            except (NotConnected, OSError):
                continue
            self.on_event("connection", "resumed")
            self.enable_udp()                                   # the old UDP binding died with the old session
            return

    # ------------------------------------------------------------------
    # UDP
    # ------------------------------------------------------------------

    def enable_udp(self) -> bool:
        """Register this client's UDP address with the server (report section 2.6).

        REQUEST_UDP_BIND over TCP returns a nonce. UDP_HELLO carries it over
        UDP, and is repeated up to five times because UDP may lose it. The
        server confirms over TCP with UDP_BOUND. Returns True once bound.
        If it fails the game still works: the countdown is then estimated
        from the last TCP STATE.
        """
        try:
            bind = self.request("REQUEST_UDP_BIND", {})
        except (RequestFailed, NotConnected):
            return False
        server = (self.server_ip, bind["server_udp_port"])
        if self._udp_sock is None:
            self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_sock.bind(("0.0.0.0", 0))                 # any free port; the server learns it from UDP_HELLO
            self._udp_sock.settimeout(0.5)
            self._filter = udp.LiveStatusFilter(server)
            threading.Thread(target=self._udp_loop, name="udp receiver", daemon=True).start()
        self.udp_bound = False
        with self._lock:
            data, room_id = self.state, self.identity["room_id"]
        if data and data["phase"] in ("OPEN", "PAUSED") and data["current_question"]:
            q = data["current_question"]
            self._filter.set_context(room_id=room_id, session_id=self.identity["session_id"],
                                     question_id=q["question_id"], attempt_id=q["attempt_id"],
                                     control_rev=data["control_rev"])
        hello = udp.encode_datagram({"v": 1, "type": "UDP_HELLO", "session_id": self.identity["session_id"],
                                     "binding_id": bind["binding_id"], "data": {"nonce": bind["udp_nonce"]}})
        for _ in range(5):
            try:
                self._udp_sock.sendto(hello, server)
            except OSError:
                return False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if self.udp_bound:
                    return True
                time.sleep(0.02)
        return False

    def _udp_loop(self) -> None:
        sock = self._udp_sock
        while not self._closing:
            try:
                payload, source = sock.recvfrom(udp.RECV_BUFFER)
            except socket.timeout:
                self._check_udp_quiet()
                continue
            except OSError:
                return
            message, verdict = self._filter.check(payload, source)
            if verdict != udp.ACCEPTED:
                self.udp_dropped[verdict] = self.udp_dropped.get(verdict, 0) + 1
                continue
            data = message["data"]
            with self._lock:
                self.live = data
                running = self.state is not None and self.state["phase"] == "OPEN"
                self._sample = (data["remaining_ms"], time.monotonic(), running)
            self._last_snapshot = time.monotonic()
            self.on_event("live", data)

    def _check_udp_quiet(self) -> None:
        """UDP silence is not a disconnect. After 3 s, refresh once over TCP (at most every 5 s)."""
        now = time.monotonic()
        with self._lock:
            active = self.state is not None and self.state["phase"] in ("OPEN", "PAUSED")
        if (active and self.udp_bound and now - self._last_snapshot > UDP_QUIET_SECONDS
                and now - self._last_refresh > UDP_REFRESH_SECONDS):
            self._last_refresh = now
            try:
                self.request("GET_STATE", {}, timeout=3)
            except (RequestFailed, NotConnected):
                pass

    # ------------------------------------------------------------------
    # What the interface reads
    # ------------------------------------------------------------------

    def countdown_ms(self) -> int:
        """Estimated time left on the current question, for display only.

        Between snapshots the client counts down locally from the last value
        the server sent. The server alone decides when the question closes.
        """
        with self._lock:
            sample = self._sample
        if sample is None:
            return 0
        remaining, at, running = sample
        if not running:
            return remaining
        return max(0, remaining - int((time.monotonic() - at) * 1000))

    def countdown_is_stale(self) -> bool:
        """True if UDP is bound but no snapshot arrived recently (show the time as approximate)."""
        return self.udp_bound and time.monotonic() - self._last_snapshot > UDP_QUIET_SECONDS
