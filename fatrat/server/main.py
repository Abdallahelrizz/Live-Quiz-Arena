"""Fat-Rat game server: sockets, threads and request routing.

Run from the repository folder:

    python3 -m fatrat.server

Threads
-------
  main thread            accepts TCP connections, forever, until Ctrl+C
  reader thread          one per TCP connection: receive a frame, check it,
                         route it to the room
  writer thread          one per TCP connection: writes queued replies
                         (connection.py)
  timer thread           one per room: question deadline and LIVE_STATUS
                         (timer.py)
  UDP thread             one for the whole server: receives UDP_HELLO
                         (udp_service.py)
  heartbeat thread       one for the whole server: PING every connection,
                         drop the ones that stop answering

Every thread is a daemon thread, so stopping the main thread with Ctrl+C
stops the whole server.

What one reader thread does with each message
---------------------------------------------
  1. recv_frame: read exactly one frame from the TCP byte stream
     (4-byte length, then that many bytes). TCP has no message boundaries,
     so one recv() may return half a message or several; framing.py loops
     until it has exactly one.
  2. decode_json + validate_message: reject anything that is not a valid
     message of the protocol. A broken byte stream (bad length, bad JSON)
     is fatal for THIS connection only; a well-formed but wrong request
     gets an ERROR reply and the connection stays open.
  3. dispatch: find the room and let it handle the request under its lock.

An error in one connection never stops the server or touches another room:
each reader thread catches its own errors and only closes its own socket.
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from typing import Optional

from fatrat.common import TCP_PORT, UDP_PORT, detect_lan_ip, log, say
from fatrat.protocol.catalogue import TCP, UNATTACHED_REQUESTS
from fatrat.protocol.framing import (
    PARTIAL_FRAME_TIMEOUT, ConnectionClosed, FrameLengthError, FrameTimeoutError,
    PayloadError, decode_json, recv_frame,
)
from fatrat.protocol.messages import ProtocolError, is_pin, validate_message
from fatrat.server import timer
from fatrat.server.connection import PING_INTERVAL, PONG_TIMEOUT, Connection, error
from fatrat.server.registry import Registry
from fatrat.server.udp_service import UdpService


class GameServer:
    def __init__(self, ip: str, tcp_port: int = TCP_PORT, udp_port: int = UDP_PORT,
                 interval: float = timer.LIVE_INTERVAL, partial_timeout: float = PARTIAL_FRAME_TIMEOUT,
                 ping_interval: float = PING_INTERVAL, pong_timeout: float = PONG_TIMEOUT,
                 impair_udp: bool = False):
        """Create the sockets. Nothing is served until serve_forever() runs.

        ip is the address clients use to reach this machine. TCP listens on
        every interface; UDP is bound to `ip` so LIVE_STATUS leaves from the
        same address the clients connected to.
        """
        self.ip, self.tcp_port, self.udp_port = ip, tcp_port, udp_port
        self.interval, self.partial_timeout = interval, partial_timeout
        self.ping_interval, self.pong_timeout = ping_interval, pong_timeout
        self.connections = set()                      # every open connection, for the heartbeat thread
        self.connections_lock = threading.Lock()
        self.stopping = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Lets the server restart at once on the same port after it was stopped.
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", tcp_port))
        self.listener.listen(128)                     # backlog: 50 players may connect at the same moment
        self.udp = UdpService(ip, udp_port, impair=impair_udp)
        # Port 0 means "any free port" (used by the automated tests): read back the real ones.
        self.tcp_port, self.udp_port = self.listener.getsockname()[1], self.udp.port
        self.registry = Registry(self.udp, lambda room: timer.start(room, self.udp, self.interval))

    def serve_forever(self) -> None:
        """Accept connections until the listener is closed (Ctrl+C in main())."""
        threading.Thread(target=self.udp.recv_loop, name="udp", daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, name="heartbeat", daemon=True).start()
        try:
            while True:
                try:
                    sock, peer = self.listener.accept()
                except OSError:
                    return                            # listener closed: stopping
                threading.Thread(target=self.handle_connection, args=(sock, peer),
                                 name=f"reader {peer[0]}:{peer[1]}", daemon=True).start()
        finally:
            self.close()

    def close(self) -> None:
        self.stopping.set()
        self.listener.close()
        self.udp.close()

    def heartbeat_loop(self) -> None:
        """Heartbeat thread: PING quiet connections and drop those that do not answer.

        Dropping only closes the socket. The connection's own reader thread
        then runs the normal disconnect path, so a silent loss is treated
        exactly like a visible one (player keeps its place; host loss pauses).
        """
        while not self.stopping.wait(min(1.0, self.ping_interval / 2)):
            with self.connections_lock:
                connections = list(self.connections)      # copy, so no socket work happens under the lock
            now = time.monotonic()
            for conn in connections:
                if not conn.heartbeat(now, self.ping_interval, self.pong_timeout):
                    log(conn.who, "no PONG in time: treating the connection as lost")
                    conn.shutdown()

    # ------------------------------------------------------------------
    # One TCP connection
    # ------------------------------------------------------------------

    def handle_connection(self, sock: socket.socket, peer) -> None:
        """Reader thread for one TCP connection."""
        # Send small messages at once instead of waiting to batch them (Nagle's algorithm off).
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn = Connection(sock, peer)
        who = conn.who
        with self.connections_lock:
            self.connections.add(conn)
        log(who, "connected")
        try:
            while True:
                try:
                    payload = recv_frame(sock, self.partial_timeout)
                except ConnectionClosed as exc:
                    log(who, "disconnected" + (" in the middle of a frame" if exc.mid_frame else ""))
                    return
                except FrameLengthError as exc:
                    # The length itself is wrong, so the stream position is lost. Close without a reply.
                    log(who, f"FATAL invalid frame length {exc.length}: closing this connection")
                    return
                except FrameTimeoutError:
                    log(who, "FATAL frame not completed in time: ERROR, then close")
                    conn.send(error("INVALID_MESSAGE", "incomplete frame timed out", None))
                    return
                except OSError:
                    return                            # reset by peer, or closed by our own writer
                try:
                    message = decode_json(payload)
                    validate_message(message, transport=TCP, from_server=False)
                except PayloadError as exc:
                    log(who, f"FATAL {exc}: ERROR, then close")
                    conn.send(error("INVALID_MESSAGE", str(exc), None))
                    return
                except ProtocolError as exc:
                    log(who, f"{'FATAL' if exc.fatal else 'rejected'}: {exc.code} ({exc.detail})")
                    conn.send(error(exc.code, exc.detail, exc.reply_to))
                    if exc.fatal:
                        return
                    continue
                try:
                    note = self.dispatch(conn, message)
                except Exception as exc:              # a bug must not kill the server or other rooms
                    log(who, f"internal error handling {message['type']}: {exc!r}")
                    return
                if note:
                    log(who, f"{message['type']} -> {note}")
        finally:
            with self.connections_lock:
                self.connections.discard(conn)
            member = conn.member
            if member is not None:
                member.room.disconnected(conn)
            conn.close()                              # flush what is queued, then close the socket

    def dispatch(self, conn: Connection, msg: dict) -> Optional[str]:
        """Route one valid message. Returns a short text for the log."""
        mtype, rid = msg["type"], msg.get("request_id")
        if mtype == "PONG":
            conn.pong(msg["data"]["ping_id"])
            return None
        wait_ms = conn.allow_request()
        if wait_ms:
            conn.send(error("RATE_LIMITED", "too many requests; slow down", rid,
                            retryable=True, retry_after_ms=wait_ms))
            return "RATE_LIMITED"
        member = conn.member
        if mtype in UNATTACHED_REQUESTS:
            if member is not None:
                conn.send(error("ALREADY_ATTACHED", "this connection is already in a room", rid))
                return "ALREADY_ATTACHED"
            if mtype == "CREATE_ROOM":
                return self.registry.create(conn, msg)
            if mtype == "JOIN_ROOM":
                pin = msg["data"]["pin"]
                room = self.registry.find_pin(pin) if is_pin(pin) else None
                if room is None:
                    conn.send(error("INVALID_PIN", "no room has that PIN", rid))
                    return f"INVALID_PIN ({pin!r})"
                return f"room {room.pin}: " + room.join(conn, msg)
            room = self.registry.find_id(msg["data"]["room_id"])          # RESUME_SESSION
            if room is None:
                conn.send(error("INVALID_TOKEN", "unknown room or token", rid))
                return "INVALID_TOKEN (unknown room)"
            return f"room {room.pin}: " + room.resume(conn, msg)
        if member is None:
            conn.send(error("NOT_AUTHENTICATED", "create or join a room first", rid))
            return "NOT_AUTHENTICATED"
        room = member.room
        if msg["room_id"] != room.room_id:
            conn.send(error("ROOM_MISMATCH", "this connection belongs to another room", rid, room.room_id))
            return "ROOM_MISMATCH"
        if mtype == "REQUEST_UDP_BIND":
            return room.bind_udp(conn, rid, self.udp_port)
        return f"room {room.pin} {member.role.lower()} {member.username!r}: " + self.registry.handle(conn, room, msg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat game server.")
    parser.add_argument("--ip", help="IPv4 address clients use to reach this machine (default: detected)")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT)
    parser.add_argument("--udp-port", type=int, default=UDP_PORT)
    parser.add_argument("--interval", type=float, default=timer.LIVE_INTERVAL,
                        help="seconds between LIVE_STATUS snapshots (default 1)")
    parser.add_argument("--partial-timeout", type=float, default=PARTIAL_FRAME_TIMEOUT,
                        help="seconds allowed to finish a frame once it has started (default 10)")
    parser.add_argument("--impair-udp", action="store_true",
                        help="TEST ONLY: drop, duplicate and reorder LIVE_STATUS on purpose")
    args = parser.parse_args()
    ip = args.ip or detect_lan_ip()
    try:
        server = GameServer(ip, args.tcp_port, args.udp_port, args.interval, args.partial_timeout,
                            impair_udp=args.impair_udp)
    except OSError as exc:
        say(f"Cannot start: {exc}. Is another server already using port {args.tcp_port} or {args.udp_port}?")
        raise SystemExit(1)
    say("Fat-Rat game server")
    say(f"  Server IPv4 address : {ip}" + ("" if args.ip else "   (detected)"))
    say(f"  TCP port {args.tcp_port} on all interfaces, UDP port {args.udp_port} on {ip}")
    say(f"  Wireshark filter    : tcp.port == {args.tcp_port} || udp.port == {args.udp_port}")
    if args.impair_udp:
        say("  TEST MODE: UDP datagrams are deliberately dropped, duplicated and reordered.")
    say("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say("\nServer stopped. All rooms were held in memory and are gone.")


if __name__ == "__main__":
    main()
