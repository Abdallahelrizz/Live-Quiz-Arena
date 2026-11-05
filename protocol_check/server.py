"""Protocol check server: a scripted peer that speaks the Fat-Rat protocol.

This is NOT the game server. It lets the protocol from report section 2 be
tested over a real network before the game is built. Exactly as the report
specifies, it does:

  * TCP framing and validation of every message, with ERROR replies;
    fatal errors close only the offending connection;
  * JOIN_ROOM into one test room (INVALID_PIN, INVALID_USERNAME, NAME_TAKEN);
  * GET_STATE, REQUEST_UDP_BIND and the UDP_HELLO -> UDP_BOUND handshake;
  * a scripted one-question session for each joined player: STATE OPEN,
    LIVE_STATUS snapshots over UDP, SUBMIT_ANSWER with INVALID_CHOICE,
    ALREADY_ANSWERED, STALE_ATTEMPT and ANSWER_LATE, scoring at close and
    STATE RESULTS. Four simulated players who never answer share each
    session, so "everyone answered" cannot close the question early;
  * deliberate UDP impairment, so the client's LIVE_STATUS filter is tested:
    seq 3 lost, seq 5 duplicated, seq 8 delivered after seq 9, and bad copies
    (wrong binding, wrong source port, stale attempt, future control_rev,
    oversized, truncated) sent just before seq 6, 7, 10, 11, 12 and 13.

It also hosts real rooms (rooms.py): a host sends CREATE_ROOM, players join
with that room's PIN, and the host loads a quiz and runs it. That is used by
rooms_client.py to test the 50-player maximum, the 5-player start gate and
several rooms at the same time.

Host commands from a player get NOT_HOST, as the protocol requires. Valid
messages this check server does not implement (pause, editing, removal,
reconnect, ...) get INVALID_STATE with a message saying so.

Threads: the main thread accepts TCP connections; each connection has a
reader thread and a writer thread; one thread receives UDP; each player
session has a question thread. A session's state is changed only while its
lock is held. Replies are queued while the lock is held and written by the
connection's writer thread, so frames never interleave and always leave in
the order the state changed, and no socket write happens under a lock.

Run from the repository folder:  python3 -m protocol_check.server
"""

from __future__ import annotations

import argparse
import hmac
import queue
import secrets
import socket
import threading
import time
import unicodedata
from typing import Optional

from fatrat.protocol import udp
from fatrat.protocol.catalogue import CATALOGUE, TCP, UNATTACHED_REQUESTS
from fatrat.protocol.framing import (
    ConnectionClosed, FrameLengthError, FrameTimeoutError, PayloadError, decode_json,
    encode_frame, encode_json, recv_frame,
)
from fatrat.protocol.messages import ProtocolError, is_pin, validate_message
from protocol_check.common import (
    TCP_PORT, UDP_PORT, detect_lan_ip, log, new_id, new_token, now_ms, say, score,
)
from protocol_check.rooms import Member, RoomManager

SIMULATED_PLAYERS = ("sim-1", "sim-2", "sim-3", "sim-4")
NOT_SUPPORTED = "not supported by the protocol check server (it is not the game server)"


def error(code: str, text: str, reply_to: Optional[str], room_id: Optional[str] = None, **extra) -> dict:
    message = {"v": 1, "type": "ERROR", "reply_to": reply_to,
               "data": {"code": code, "message": text[:200], "retryable": False, **extra}}
    if room_id:
        message["room_id"] = room_id
    return message


def result(reply_to: str, room_id: str, data: dict) -> dict:
    return {"v": 1, "type": "RESULT", "reply_to": reply_to, "room_id": room_id, "data": data}


class Connection:
    """One TCP connection with an ordered outgoing queue and its own writer thread.

    send() never blocks, so it is safe to call while holding a session lock.
    A slow client whose queue fills up is disconnected instead of blocking.
    """

    _CLOSE = object()

    def __init__(self, sock: socket.socket, peer):
        self.sock = sock
        self.peer = peer
        self._queue: "queue.Queue" = queue.Queue(maxsize=1000)
        threading.Thread(target=self._write_loop, daemon=True).start()

    def send(self, message: dict) -> None:
        # Self-check: never put a message on the wire that breaks our own protocol.
        validate_message(message, transport=TCP, from_server=True, strict=True)
        self._enqueue(encode_frame(message))

    def close(self) -> None:
        """Close after everything already queued has been written."""
        self._enqueue(self._CLOSE)

    def _enqueue(self, item) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            log(self.peer[0], "writer queue full: disconnecting slow client")
            self._shutdown()

    def _write_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._CLOSE:
                break
            try:
                self.sock.sendall(item)
            except OSError:
                break
        self._shutdown()

    def _shutdown(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)   # also wakes the reader thread
        except OSError:
            pass
        self.sock.close()


class Session:
    """One joined player and its scripted one-question quiz."""

    def __init__(self, conn: Connection, username: str, question_ms: int):
        self.conn = conn
        self.username = username
        self.actor_id = new_id()
        self.session_id = new_id()
        self.token = new_token()
        self.lock = threading.Lock()
        self.state_rev = 0
        self.control_rev = 0
        self.phase = "LOBBY"
        choice_ids = [new_id() for _ in range(4)]
        self.question = {
            "question_id": new_id(),
            "text": "Which transport carries Fat-Rat answers?",       # host-only, never sent to players
            "choices": [{"choice_id": cid, "text": t} for cid, t in zip(choice_ids, ("TCP", "UDP", "HTTP", "ICMP"))],
            "correct_choice_id": secrets.choice(choice_ids),
            "time_limit_ms": question_ms,
        }
        self.attempt_id: Optional[str] = None
        self.deadline_ms = 0
        self.answer: Optional[dict] = None          # {choice_id, remaining_ms, request_id}
        self.last_result: Optional[dict] = None
        self.total_score = 0
        self.simulated = {name: new_id() for name in SIMULATED_PLAYERS}
        self.binding: Optional[dict] = None         # {binding_id, nonce, expires_ms, udp_addr, confirmed, seq}
        self.question_started = False
        self.closed = False


class CheckServer:
    def __init__(self, ip: str, tcp_port: int, udp_port: int, question_ms: int,
                 interval: float, partial_timeout: float):
        self.ip, self.tcp_port, self.udp_port = ip, tcp_port, udp_port
        self.question_ms, self.interval, self.partial_timeout = question_ms, interval, partial_timeout
        self.room_id = new_id()
        self.pin = f"{secrets.randbelow(1_000_000):06d}"
        self.registry_lock = threading.Lock()        # protects names and bindings
        self.names = {n.casefold() for n in SIMULATED_PLAYERS}
        self.bindings = {}                           # binding_id -> Session or room Member
        self.rooms = RoomManager(self, interval)     # real rooms created with CREATE_ROOM
        self.host_types = {name for name, spec in CATALOGUE.items() if spec.sender == "H"}

    def room_id_of(self, attachment) -> str:
        return attachment.room.room_id if isinstance(attachment, Member) else self.room_id

    def forget_binding(self, binding_id: str) -> None:
        with self.registry_lock:
            self.bindings.pop(binding_id, None)

    def send_udp(self, payload: bytes, addr) -> None:
        try:
            self.udp_sock.sendto(payload, addr)
        except OSError as exc:
            log("UDP send failed:", exc)

    # ------------------------------------------------------------------
    # Sockets and threads
    # ------------------------------------------------------------------

    def serve_forever(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", self.tcp_port))   # TCP may listen on every interface
        listener.listen(64)
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind((self.ip, self.udp_port))  # UDP bound to the advertised address
        self.spoof_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.spoof_sock.bind((self.ip, 0))           # sends the "wrong source port" test datagram
        threading.Thread(target=self.udp_loop, daemon=True).start()
        try:
            while True:
                sock, peer = listener.accept()
                threading.Thread(target=self.handle_connection, args=(sock, peer), daemon=True).start()
        finally:
            listener.close()
            self.udp_sock.close()
            self.spoof_sock.close()

    def handle_connection(self, sock: socket.socket, peer) -> None:
        """Reader thread for one TCP connection: frame -> decode -> validate -> dispatch."""
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        who = f"{peer[0]}:{peer[1]}"
        conn = Connection(sock, peer)
        session: Optional[Session] = None
        log(who, "connected")
        try:
            while True:
                try:
                    payload = recv_frame(sock, self.partial_timeout)
                except ConnectionClosed as exc:
                    log(who, "disconnected" + (" in the middle of a frame" if exc.mid_frame else ""))
                    return
                except FrameLengthError as exc:
                    log(who, f"FATAL invalid frame length {exc.length}: closing without a reply")
                    return
                except FrameTimeoutError:
                    log(who, f"FATAL frame incomplete after {self.partial_timeout:g} s: ERROR then close")
                    conn.send(error("INVALID_MESSAGE", "incomplete frame timed out", None))
                    return
                except OSError:
                    return
                try:
                    message = decode_json(payload)
                    validate_message(message, transport=TCP, from_server=False)
                except PayloadError as exc:
                    log(who, f"FATAL {exc}: ERROR then close")
                    conn.send(error("INVALID_MESSAGE", str(exc), None))
                    return
                except ProtocolError as exc:
                    kind = "FATAL" if exc.fatal else "rejected"
                    log(who, f"{kind} {message.get('type')!r}: {exc.code} ({exc.detail})")
                    conn.send(error(exc.code, exc.detail, exc.reply_to))
                    if exc.fatal:
                        return
                    continue
                session = self.dispatch(conn, session, message, who)
        finally:
            if isinstance(session, Member):
                self.rooms.disconnected(session)
            elif session is not None:
                with session.lock:
                    session.closed = True
                    binding = session.binding
                if binding:
                    self.forget_binding(binding["binding_id"])
            conn.close()

    # ------------------------------------------------------------------
    # TCP requests
    # ------------------------------------------------------------------

    def dispatch(self, conn: Connection, s: Optional[Session], msg: dict, who: str) -> Optional[Session]:
        mtype, rid = msg["type"], msg.get("request_id")
        if mtype == "PONG":
            return s
        if mtype in UNATTACHED_REQUESTS and s is not None:
            log(who, f"{mtype} on an attached connection -> ALREADY_ATTACHED")
            conn.send(error("ALREADY_ATTACHED", "this connection is already attached", rid))
            return s
        if mtype == "JOIN_ROOM":
            if msg["data"]["pin"] == self.pin:
                return self.join(conn, msg, who)          # scripted test room
            return self.rooms.join(conn, msg, who)        # a room created by a host
        if mtype == "CREATE_ROOM":
            return self.rooms.create(conn, msg, who)
        if mtype in UNATTACHED_REQUESTS:
            log(who, f"{mtype} -> INVALID_STATE (not supported here)")
            conn.send(error("INVALID_STATE", f"{mtype} {NOT_SUPPORTED}", rid))
            return s
        if s is None:
            log(who, f"{mtype} before joining -> NOT_AUTHENTICATED")
            conn.send(error("NOT_AUTHENTICATED", "join a room first", rid))
            return s
        if msg["room_id"] != self.room_id_of(s):
            log(who, f"{mtype} for another room -> ROOM_MISMATCH")
            conn.send(error("ROOM_MISMATCH", "request names another room", rid, self.room_id_of(s)))
            return s

        if mtype == "REQUEST_UDP_BIND":
            self.bind_udp(s, rid, who)
            return s
        if isinstance(s, Member):
            self.rooms.handle(s, msg, who)
            return s
        with s.lock:
            self.close_if_due(s)
            if mtype == "SUBMIT_ANSWER":
                self.submit(s, msg, who)
                return s
            if mtype == "GET_STATE":
                conn.send(result(rid, self.room_id, {}))
                conn.send(self.state(s, recovery=True))
                outcome = "RESULT + STATE"
            elif CATALOGUE[mtype].sender == "H":
                conn.send(error("NOT_HOST", f"{mtype} is a host command", rid, self.room_id))
                outcome = "NOT_HOST"
            else:
                conn.send(error("INVALID_STATE", f"{mtype} {NOT_SUPPORTED}", rid, self.room_id))
                outcome = "INVALID_STATE (not supported here)"
        log(who, f"{mtype} -> {outcome}")
        return s

    def join(self, conn: Connection, msg: dict, who: str) -> Optional[Session]:
        data, rid = msg["data"], msg["request_id"]
        # Report order: PIN, (finished room), name format, name taken, (room full).
        if not is_pin(data["pin"]) or data["pin"] != self.pin:
            log(who, f"JOIN_ROOM wrong PIN {data['pin']!r} -> INVALID_PIN")
            conn.send(error("INVALID_PIN", "no room has that PIN", rid))
            return None
        name = unicodedata.normalize("NFC", data["username"]).strip()
        if not 1 <= len(name) <= 32:
            conn.send(error("INVALID_USERNAME", "names must be 1..32 characters", rid))
            return None
        with self.registry_lock:
            taken = name.casefold() in self.names
            if not taken:
                self.names.add(name.casefold())
        if taken:
            log(who, f"JOIN_ROOM name {name!r} already used -> NAME_TAKEN")
            conn.send(error("NAME_TAKEN", "that name is already used in this room", rid))
            return None
        s = Session(conn, name, self.question_ms)
        with s.lock:
            s.state_rev = 1
            conn.send(result(rid, self.room_id, {
                "room_id": self.room_id, "pin": self.pin, "session_id": s.session_id,
                "actor_id": s.actor_id, "role": "PLAYER", "reconnect_token": s.token}))
            conn.send(self.state(s))
        log(who, f"JOIN_ROOM {name!r} joined -> RESULT + STATE LOBBY")
        return s

    def bind_udp(self, s, rid: str, who: str) -> None:
        """REQUEST_UDP_BIND for a scripted Session or a room Member (both have lock/binding/conn)."""
        binding = {"binding_id": new_id(), "nonce": secrets.token_hex(16), "expires_ms": now_ms() + 10_000,
                   "udp_addr": None, "confirmed": False, "seq": 0}
        with s.lock:
            old, s.binding = s.binding, binding      # a new bind replaces the old one
            s.conn.send(result(rid, self.room_id_of(s), {
                "binding_id": binding["binding_id"], "udp_nonce": binding["nonce"],
                "server_udp_port": self.udp_port}))
        with self.registry_lock:                     # registry lock is never taken inside a session lock
            if old:
                self.bindings.pop(old["binding_id"], None)
            self.bindings[binding["binding_id"]] = s
        log(who, "REQUEST_UDP_BIND -> RESULT (binding + nonce)" + (", old binding retired" if old else ""))

    def submit(self, s: Session, msg: dict, who: str) -> None:
        """SUBMIT_ANSWER, in the report's validation order. Caller holds s.lock."""
        data, rid = msg["data"], msg["request_id"]
        conn, q = s.conn, s.question
        if s.answer and s.answer["request_id"] == rid and s.answer["data"] == data:
            conn.send(result(rid, self.room_id, self.acceptance(s)))      # exact replay: original reply
            outcome = "replay of accepted answer -> original RESULT"
        elif s.phase == "LOBBY":
            conn.send(error("INVALID_STATE", "no question is open", rid, self.room_id))
            outcome = "INVALID_STATE"
        elif data["question_id"] != q["question_id"] or data["attempt_id"] != s.attempt_id:
            conn.send(error("STALE_ATTEMPT", "that question attempt is not current", rid, self.room_id))
            conn.send(self.state(s))
            outcome = "STALE_ATTEMPT + fresh STATE"
        elif s.phase == "RESULTS":
            conn.send(error("ANSWER_LATE", "the question has closed", rid, self.room_id))
            outcome = "ANSWER_LATE"
        elif data["choice_id"] not in [c["choice_id"] for c in q["choices"]]:
            conn.send(error("INVALID_CHOICE", "not a choice of this question", rid, self.room_id))
            outcome = "INVALID_CHOICE"
        elif s.answer:
            conn.send(error("ALREADY_ANSWERED", "one answer per question", rid, self.room_id,
                            question_id=q["question_id"], attempt_id=s.attempt_id,
                            choice_id=s.answer["choice_id"]))
            outcome = "ALREADY_ANSWERED"
        else:
            # Acceptance time is sampled here, under the lock (report section 2.4).
            s.answer = {"choice_id": data["choice_id"], "remaining_ms": s.deadline_ms - now_ms(),
                        "request_id": rid, "data": dict(data)}
            conn.send(result(rid, self.room_id, self.acceptance(s)))
            outcome = f"accepted with {s.answer['remaining_ms']} ms left"
        log(who, f"SUBMIT_ANSWER -> {outcome}")

    def acceptance(self, s: Session) -> dict:
        return {"accepted": True, "question_id": s.question["question_id"], "attempt_id": s.attempt_id,
                "choice_id": s.answer["choice_id"]}

    # ------------------------------------------------------------------
    # Scripted question, scoring and STATE
    # ------------------------------------------------------------------

    def close_if_due(self, s: Session) -> None:
        """Close an OPEN question whose deadline has passed. Caller holds s.lock.

        Every handler calls this first, so a late answer can never be accepted
        even if the question thread is slow to wake up.
        """
        if s.phase != "OPEN" or now_ms() < s.deadline_ms:
            return
        q = s.question
        choice = s.answer["choice_id"] if s.answer else None
        correct = choice == q["correct_choice_id"]
        points = score(s.answer["remaining_ms"], q["time_limit_ms"]) if correct else 0
        s.total_score += points
        s.last_result = {"question_id": q["question_id"], "attempt_id": s.attempt_id,
                         "correct_choice_id": q["correct_choice_id"], "reason": "TIMEOUT",
                         "own_result": {"choice_id": choice, "correct": correct, "points": points,
                                        "total_score": s.total_score}}
        s.phase = "RESULTS"
        s.control_rev += 1
        s.state_rev += 1
        s.conn.send(self.state(s))
        log(s.conn.peer[0], f"question closed (TIMEOUT): {s.username} {'correct' if correct else 'wrong/none'}, "
                            f"{points} points -> STATE RESULTS")

    def state(self, s: Session, recovery: bool = False) -> dict:
        """Player STATE snapshot built from the session's committed state. Caller holds s.lock."""
        q = s.question
        current = None
        if s.phase != "LOBBY":
            current = {"question_id": q["question_id"], "attempt_id": s.attempt_id,
                       "time_limit_ms": q["time_limit_ms"],
                       "choices": [{"choice_id": c["choice_id"], "symbol_index": i}
                                   for i, c in enumerate(q["choices"])]}
        live = None
        if recovery and s.phase == "OPEN":
            live = {"answered_count": 1 if s.answer else 0, "eligible_count": 1 + len(s.simulated)}
        players = [(s.total_score, s.username, s.actor_id)] + [(0, n, pid) for n, pid in s.simulated.items()]
        leaderboard = []
        for points in sorted({p[0] for p in players}, reverse=True):      # dense ranks, ties on one row
            tied = sorted((p for p in players if p[0] == points), key=lambda p: p[1].casefold())
            leaderboard.append({"rank": len(leaderboard) + 1, "score": points, "players": [
                {"player_id": pid, "username": name, "participation": "ACTIVE"} for _, name, pid in tied]})
        return {"v": 1, "type": "STATE", "room_id": self.room_id, "state_rev": s.state_rev, "data": {
            "control_rev": s.control_rev, "phase": s.phase, "finish_reason": None, "pause_reason": None,
            "editing": False, "host_connected": True, "title": "Protocol check",
            "question_index": None if s.phase == "LOBBY" else 0, "question_count": 1,
            "current_question": current,
            "remaining_ms": max(0, s.deadline_ms - now_ms()) if s.phase == "OPEN" else 0,
            "self": {"actor_id": s.actor_id, "role": "PLAYER", "username": s.username,
                     "total_score": s.total_score,
                     "submitted_choice_id": s.answer["choice_id"] if s.answer else None},
            "leaderboard": leaderboard, "last_result": s.last_result, "live": live}}

    def run_question(self, s: Session) -> None:
        """Question thread: open the question, stream LIVE_STATUS, close at the deadline."""
        with s.lock:
            s.attempt_id = new_id()
            s.deadline_ms = now_ms() + s.question["time_limit_ms"]
            s.phase = "OPEN"
            s.control_rev += 1      # stands in for the host's START_QUIZ
            s.state_rev += 1
            s.conn.send(self.state(s))
        log(s.conn.peer[0], f"question OPEN for {s.username} ({s.question['time_limit_ms'] / 1000:g} s)")
        held, addr = None, None
        while True:
            snapshot = None
            with s.lock:
                self.close_if_due(s)
                if s.closed or s.phase != "OPEN":
                    break
                remaining = max(0, s.deadline_ms - now_ms())
                b = s.binding
                if b is not None and b["confirmed"]:
                    b["seq"] += 1
                    # All fields come from one committed state, read while the lock is held.
                    snapshot = {"v": 1, "type": "LIVE_STATUS", "room_id": self.room_id,
                                "session_id": s.session_id, "binding_id": b["binding_id"],
                                "question_id": s.question["question_id"], "attempt_id": s.attempt_id,
                                "control_rev": s.control_rev, "seq": b["seq"],
                                "data": {"remaining_ms": remaining, "answered_count": 1 if s.answer else 0,
                                         "eligible_count": 1 + len(s.simulated)}}
                    addr = b["udp_addr"]
            if snapshot is not None:
                held = self.send_impaired(snapshot, addr, held)  # socket I/O outside the lock
            time.sleep(max(0.01, min(self.interval, remaining / 1000)))
        if held and addr:
            self.udp_sock.sendto(held, addr)   # a held-back snapshot arriving after the close

    def send_impaired(self, snap: dict, addr, held: Optional[bytes]) -> Optional[bytes]:
        """Send one snapshot with the scripted loss/duplicate/reorder/bad-copy plan."""
        seq = snap["seq"]
        real = udp.encode_datagram(snap)
        sends = []
        if seq == 3:
            return held                                                    # lost
        if seq == 6:
            sends.append((self.udp_sock, encode_json({**snap, "binding_id": new_id()})))
        if seq == 7:
            sends.append((self.spoof_sock, real))                          # right content, wrong port
        if seq == 8:
            return real                                                    # held back, sent after 9
        if seq == 10:
            sends.append((self.udp_sock, encode_json({**snap, "attempt_id": new_id()})))
        if seq == 11:
            sends.append((self.udp_sock, encode_json({**snap, "control_rev": snap["control_rev"] + 1})))
        if seq == 12:
            sends.append((self.udp_sock, encode_json({**snap, "padding": "x" * 600})))   # > 512 bytes
        if seq == 13:
            sends.append((self.udp_sock, real[:-7]))                        # truncated JSON
        sends.append((self.udp_sock, real))
        if seq == 5:
            sends.append((self.udp_sock, real))                            # duplicate
        if seq == 9 and held:
            sends.append((self.udp_sock, held))                            # seq 8 arrives late
            held = None
        for sock, payload in sends:
            try:
                sock.sendto(payload, addr)
            except OSError as exc:
                log("UDP send failed:", exc)
        return held

    # ------------------------------------------------------------------
    # UDP: endpoint registration
    # ------------------------------------------------------------------

    def udp_loop(self) -> None:
        """UDP receiver thread: verify UDP_HELLO and confirm the endpoint over TCP."""
        while True:
            try:
                payload, addr = self.udp_sock.recvfrom(udp.RECV_BUFFER)
            except OSError:
                return
            message, verdict = udp.parse_datagram(payload, from_server=False)
            if message is None or message["type"] != "UDP_HELLO":
                log(addr[0], f"UDP datagram dropped silently ({verdict})")
                continue
            with self.registry_lock:
                s = self.bindings.get(message["binding_id"])
            if s is None:
                log(addr[0], "UDP_HELLO for an unknown binding dropped")
                continue
            start, problem = False, None
            with s.lock:
                b = s.binding
                if b is None or b["binding_id"] != message["binding_id"] or s.conn is None:
                    problem = "old binding"
                elif message["session_id"] != s.session_id:
                    problem = "wrong session"
                elif not hmac.compare_digest(message["data"]["nonce"], b["nonce"]):
                    problem = "wrong nonce"
                elif not b["confirmed"] and now_ms() > b["expires_ms"]:
                    problem = "challenge expired"
                elif addr[0] != s.conn.peer[0]:
                    problem = f"source IP {addr[0]} is not the TCP peer {s.conn.peer[0]}"
                elif b["udp_addr"] not in (None, addr):
                    problem = "different port needs a fresh REQUEST_UDP_BIND"
                else:
                    b["udp_addr"], b["confirmed"] = addr, True
                    s.conn.send({"v": 1, "type": "UDP_BOUND", "room_id": self.room_id_of(s),
                                 "data": {"binding_id": b["binding_id"]}})
                    if isinstance(s, Session) and not s.question_started:
                        s.question_started = start = True    # scripted session: open its question
            if problem:
                log(addr[0], f"UDP_HELLO dropped: {problem}")
                continue
            log(addr[0], f"UDP_HELLO from port {addr[1]} verified -> UDP_BOUND over TCP")
            if start:
                threading.Thread(target=self.run_question, args=(s,), daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat protocol check server (scripted test peer).")
    parser.add_argument("--ip", help="IPv4 address clients use to reach this machine (default: auto-detect)")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT)
    parser.add_argument("--udp-port", type=int, default=UDP_PORT)
    parser.add_argument("--question-seconds", type=float, default=10.0,
                        help="time limit of the scripted question (default 10)")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="seconds between LIVE_STATUS snapshots (game uses about 1; default 0.5 gives more samples)")
    parser.add_argument("--partial-timeout", type=float, default=10.0,
                        help="seconds allowed to finish a started frame (report: 10)")
    args = parser.parse_args()
    ip = args.ip or detect_lan_ip()
    server = CheckServer(ip, args.tcp_port, args.udp_port, int(args.question_seconds * 1000),
                         args.interval, args.partial_timeout)
    say("Fat-Rat protocol check server  (scripted test peer, not the game server)")
    say(f"  Server IPv4 address : {ip}" + ("" if args.ip else "   (auto-detected)"))
    say(f"  TCP port {args.tcp_port} on all interfaces, UDP port {args.udp_port} on {ip}")
    say(f"  Test room PIN       : {server.pin}")
    say("  On the other device, from the project folder, run:")
    say(f"      python3 -m protocol_check.client --server {ip} --pin {server.pin}")
    say(f"      python3 -m protocol_check.rooms_client --server {ip}")
    say(f"  Wireshark filter    : tcp.port == {args.tcp_port} || udp.port == {args.udp_port}")
    say("  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say("\nServer stopped.")


if __name__ == "__main__":
    main()
