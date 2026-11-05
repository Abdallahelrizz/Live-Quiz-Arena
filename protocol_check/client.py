"""Protocol check client: tests the Fat-Rat protocol against the check server.

Run it on a different device from the server (that is the point), from the
repository folder:

    python3 -m protocol_check.client --server 192.168.1.35 --pin 123456

It connects over TCP and UDP and runs these checks, printing PASS/FAIL:

  T  TCP framing: a message sent one byte at a time, a message split inside
     its 4-byte header, three requests glued into one send, duplicate name
  V  invalid messages that must get an ERROR while the connection stays open
  U  UDP: the UDP_HELLO/UDP_BOUND handshake, then LIVE_STATUS snapshots the
     server deliberately loses, duplicates, reorders and corrupts
  A  answers: invalid choice, accepted answer, second answer, replay, stale
     attempt, scoring at close, late answer
  F  fatal input (bad JSON, duplicate keys, wrong version, bad lengths, stalled
     frame) that must close only that one connection, and a final check that
     the server still accepts new clients

Every message received from the server is also validated against the
catalogue (envelope, data fields, RESULT per request, STATE rules); any
mismatch is reported as a failure.
"""

from __future__ import annotations

import argparse
import queue
import random
import socket
import struct
import sys
import threading
import time
from typing import Callable, List, Optional

from fatrat.protocol import udp
from fatrat.protocol.catalogue import TCP
from fatrat.protocol.framing import (
    ConnectionClosed, FramingError, encode_frame, encode_json, recv_message,
)
from fatrat.protocol.messages import ProtocolError, validate_message, validate_result, validate_state
from protocol_check.common import TCP_PORT, UDP_PORT, new_id, say


# --------------------------------------------------------------------------
# TCP link with a background reader
# --------------------------------------------------------------------------

class Link:
    """One TCP connection to the server.

    A reader thread decodes every incoming frame, validates it and puts it in
    an inbox. wait_for() takes the next message matching a condition and keeps
    the others for later, because asynchronous messages (STATE OPEN,
    UDP_BOUND, ...) can arrive between replies.
    """

    def __init__(self, server: str, port: int, on_message: Optional[Callable[[dict], None]] = None,
                 role: str = "PLAYER"):
        self.role = role               # STATE snapshots are checked for this recipient role
        self.sock = socket.create_connection((server, port), timeout=5)
        self.sock.settimeout(None)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # send small writes at once
        self.on_message = on_message
        self.inbox: "queue.Queue" = queue.Queue()
        self.backlog: List[dict] = []
        self.pending = {}              # request_id -> request type, to validate the RESULT
        self.problems: List[str] = []  # protocol violations seen in server messages
        self.closed = threading.Event()
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        while True:
            try:
                message = recv_message(self.sock)
            except ConnectionClosed:
                break
            except (FramingError, OSError) as exc:
                if not isinstance(exc, OSError):
                    self.problems.append(f"bad framing from server: {exc}")
                break
            try:
                validate_message(message, transport=TCP, from_server=True, strict=True)
                if message["type"] == "STATE":
                    validate_state(message, role=self.role)
                elif message["type"] == "RESULT" and message["reply_to"] in self.pending:
                    validate_result(message["data"], self.pending[message["reply_to"]])
            except ProtocolError as exc:
                self.problems.append(f"{message.get('type')}: {exc.detail}")
            if self.on_message:
                self.on_message(message)
            self.inbox.put(message)
        self.closed.set()
        self.inbox.put(None)

    def request(self, mtype: str, data: dict, room_id: Optional[str] = None) -> dict:
        message = {"v": 1, "type": mtype, "request_id": new_id(), "data": data}
        if room_id:
            message["room_id"] = room_id
        self.pending[message["request_id"]] = mtype
        return message

    def send(self, message: dict) -> None:
        self.sock.sendall(encode_frame(message))

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def wait_for(self, condition: Callable[[dict], bool], timeout: float = 5.0) -> Optional[dict]:
        for i, message in enumerate(self.backlog):
            if condition(message):
                return self.backlog.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self.inbox.get(timeout=remaining)
            except queue.Empty:
                return None
            if message is None:            # connection closed
                self.inbox.put(None)
                return None
            if condition(message):
                return message
            self.backlog.append(message)

    def reply(self, request: dict, timeout: float = 5.0) -> Optional[dict]:
        return self.wait_for(lambda m: m.get("reply_to") == request["request_id"], timeout)

    def next(self, mtype: str, timeout: float = 5.0, phase: Optional[str] = None) -> Optional[dict]:
        return self.wait_for(lambda m: m["type"] == mtype and (phase is None or m["data"].get("phase") == phase),
                             timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Result reporting
# --------------------------------------------------------------------------

class Report:
    def __init__(self, quiet: bool = False):
        self.rows = []
        self.quiet = quiet

    def section(self, title: str) -> None:
        if not self.quiet:
            say(f"\n{title}")

    def check(self, cid: str, title: str, ok, detail: str = "") -> bool:
        ok = bool(ok)
        self.rows.append((cid, title, ok, detail))
        if not self.quiet:
            say(f"  {'PASS' if ok else 'FAIL'}  {cid:<4} {title}" + (f"\n              {detail}" if detail else ""))
        return ok

    @property
    def failed(self):
        return [row for row in self.rows if not row[2]]


def describe(message: Optional[dict]) -> str:
    if message is None:
        return "no reply"
    if message["type"] == "ERROR":
        return f"ERROR {message['data']['code']} (reply_to {'matches' if message.get('reply_to') else 'null'})"
    return message["type"]


def is_error(message: Optional[dict], code: str, request: Optional[dict] = None) -> bool:
    if not message or message["type"] != "ERROR" or message["data"]["code"] != code:
        return False
    return request is None or message.get("reply_to") == request["request_id"]


# --------------------------------------------------------------------------
# Main scenario for one player
# --------------------------------------------------------------------------

def run_player(args, name: str, report: Report, show_udp: bool) -> None:
    identity = {}
    live = udp.LiveStatusFilter((args.server, args.udp_port))
    records = []             # one entry per received datagram
    stop_udp = threading.Event()

    def on_message(message: dict) -> None:
        # Runs on the TCP reader thread, in arrival order.
        if message["type"] == "RESULT" and "session_id" in message["data"]:
            identity.update(message["data"])
        elif message["type"] == "UDP_BOUND":
            live.confirm_binding(message["data"]["binding_id"])
        elif message["type"] == "STATE":
            data, question = message["data"], message["data"]["current_question"]
            if data["phase"] in ("OPEN", "PAUSED") and question:
                live.set_context(room_id=message["room_id"], session_id=identity.get("session_id"),
                                 question_id=question["question_id"], attempt_id=question["attempt_id"],
                                 control_rev=data["control_rev"])
            else:
                live.clear_context()

    link = Link(args.server, args.tcp_port, on_message)
    report.section(f"TCP framing   (this client {link.sock.getsockname()[0]} -> server {args.server}:{args.tcp_port})")

    # T1: wrong PIN, sent one byte per send() call.
    wrong_pin = f"{(int(args.pin) + 1) % 1_000_000:06d}"
    request = link.request("JOIN_ROOM", {"pin": wrong_pin, "username": name})
    frame = encode_frame(request)
    for i in range(len(frame)):
        link.send_raw(frame[i:i + 1])
        time.sleep(0.002)
    reply = link.reply(request)
    report.check("T1", f"JOIN_ROOM sent 1 byte at a time ({len(frame)} sends) is reassembled; wrong PIN -> INVALID_PIN",
                 is_error(reply, "INVALID_PIN", request), describe(reply))

    # T2: correct join, split inside the 4-byte length header.
    request = link.request("JOIN_ROOM", {"pin": args.pin, "username": name})
    frame = encode_frame(request)
    for piece in (frame[:2], frame[2:9], frame[9:]):
        link.send_raw(piece)
        time.sleep(0.2)
    reply = link.reply(request)
    lobby = link.next("STATE")
    joined = bool(reply and reply["type"] == "RESULT" and lobby and lobby["data"]["phase"] == "LOBBY")
    report.check("T2", "JOIN_ROOM split inside its 4-byte header -> RESULT (identity) then STATE LOBBY", joined,
                 f"joined as {name!r}, PIN {reply['data']['pin']}" if joined else describe(reply))
    if not joined:
        link.close()
        return
    room = identity["room_id"]

    # T3: three requests glued into a single send().
    batch = [link.request("GET_STATE", {}, room), link.request("GET_STATE", {}, room),
             link.request("REQUEST_UDP_BIND", {}, room)]
    link.send_raw(b"".join(encode_frame(r) for r in batch))
    got = [link.wait_for(lambda m: True) for _ in range(5)]
    expected = [("RESULT", batch[0]), ("STATE", None), ("RESULT", batch[1]), ("STATE", None), ("RESULT", batch[2])]
    in_order = all(g and g["type"] == t and (r is None or g.get("reply_to") == r["request_id"])
                   for g, (t, r) in zip(got, expected))
    report.check("T3", "3 requests glued into one send -> RESULT, STATE, RESULT, STATE, RESULT in order", in_order,
                 " ".join(describe(g) for g in got))
    bind = got[4]["data"] if in_order else None

    # V: invalid messages that must not close the connection.
    report.section("Invalid messages   (each gets an ERROR; the connection stays open)")
    cases = []
    r = link.request("SUBMIT_ANSWER", {"question_id": new_id(), "attempt_id": new_id()}, room)
    cases.append(("V1", "SUBMIT_ANSWER missing choice_id -> INVALID_MESSAGE, reply_to matches", r, "INVALID_MESSAGE"))
    r = link.request("DANCE", {}, room)
    cases.append(("V2", "unknown message type -> INVALID_MESSAGE", r, "INVALID_MESSAGE"))
    r = link.request("GET_STATE", {"colour": "yellow"}, room)
    cases.append(("V3", "unknown data field -> INVALID_MESSAGE", r, "INVALID_MESSAGE"))
    r = link.request("START_QUIZ", {"expected_control_rev": True}, room)
    cases.append(("V4", "boolean where an integer is required -> INVALID_MESSAGE", r, "INVALID_MESSAGE"))
    r = link.request("START_QUIZ", {"expected_control_rev": 0}, room)
    cases.append(("V5", "valid host command from a player -> NOT_HOST", r, "NOT_HOST"))
    r = link.request("GET_STATE", {}, new_id())
    cases.append(("V6", "request naming another room -> ROOM_MISMATCH", r, "ROOM_MISMATCH"))
    for cid, title, r, code in cases:
        link.send(r)
        reply = link.reply(r)
        report.check(cid, title, is_error(reply, code, r), describe(reply))
    r = link.request("GET_STATE", {}, room)
    link.send(r)
    reply, state = link.reply(r), link.next("STATE")
    report.check("V7", "connection still works afterwards: GET_STATE -> RESULT + STATE",
                 reply and reply["type"] == "RESULT" and state)

    # U1: UDP endpoint registration.
    report.section("UDP registration and LIVE_STATUS")
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.bind(("0.0.0.0", 0))
    udp_sock.settimeout(0.3)

    def udp_receiver() -> None:
        while not stop_udp.is_set():
            try:
                payload, source = udp_sock.recvfrom(udp.RECV_BUFFER)
            except socket.timeout:
                continue
            except OSError:
                return
            message, verdict = live.check(payload, source)
            seq = message.get("seq") if isinstance(message, dict) else None
            remaining = message["data"]["remaining_ms"] if isinstance(message, dict) else None
            records.append({"seq": seq, "verdict": verdict, "remaining": remaining, "bytes": len(payload),
                            "source": source})

    threading.Thread(target=udp_receiver, daemon=True).start()
    bound = None
    hellos = 0
    if bind:
        live.server_udp_addr = (args.server, bind["server_udp_port"])
        hello = {"v": 1, "type": "UDP_HELLO", "session_id": identity["session_id"],
                 "binding_id": bind["binding_id"], "data": {"nonce": bind["udp_nonce"]}}
        while bound is None and hellos < 5:          # at most 5 HELLOs, 1 s apart (section 2.6)
            udp_sock.sendto(udp.encode_datagram(hello), live.server_udp_addr)
            hellos += 1
            bound = link.next("UDP_BOUND", timeout=1.0)
    report.check("U1", f"UDP_HELLO from UDP port {udp_sock.getsockname()[1]} -> UDP_BOUND over TCP",
                 bound and bound["data"]["binding_id"] == bind["binding_id"], f"{hellos} HELLO(s) sent")
    if not bound:
        stop_udp.set()
        link.close()
        return

    # A: answers.
    report.section("Answers")
    opened = link.next("STATE", phase="OPEN", timeout=5)
    question = opened["data"]["current_question"] if opened else None
    report.check("A1", "STATE OPEN gives the player selection symbols only (no question text, no correct answer)",
                 question and "text" not in question and "correct_choice_id" not in str(opened["data"]),
                 f"{len(question['choices'])} choices, {question['time_limit_ms'] / 1000:g} s" if question else "no STATE OPEN")
    if not question:
        stop_udp.set()
        link.close()
        return
    time.sleep(2.0)                                       # let a few LIVE_STATUS snapshots arrive first
    q_ids = {"question_id": question["question_id"], "attempt_id": question["attempt_id"]}
    choice = question["choices"][0]["choice_id"]

    r = link.request("SUBMIT_ANSWER", {**q_ids, "choice_id": new_id()}, room)
    link.send(r)
    reply = link.reply(r)
    report.check("A2", "answer with a choice that is not in the question -> INVALID_CHOICE",
                 is_error(reply, "INVALID_CHOICE", r), describe(reply))

    accepted_request = link.request("SUBMIT_ANSWER", {**q_ids, "choice_id": choice}, room)
    link.send(accepted_request)
    accepted = link.reply(accepted_request)
    report.check("A3", "valid answer -> RESULT accepted (no correctness or points revealed yet)",
                 accepted and accepted["type"] == "RESULT" and accepted["data"].get("accepted") is True
                 and "correct" not in accepted["data"], describe(accepted))

    r = link.request("SUBMIT_ANSWER", {**q_ids, "choice_id": question["choices"][1]["choice_id"]}, room)
    link.send(r)
    reply = link.reply(r)
    report.check("A4", "second answer (new request) -> ALREADY_ANSWERED naming the accepted choice",
                 is_error(reply, "ALREADY_ANSWERED", r) and reply["data"]["choice_id"] == choice, describe(reply))

    link.send(accepted_request)                       # exact same request again (lost-reply retry)
    reply = link.reply(accepted_request)
    report.check("A5", "same request re-sent -> original RESULT again (replay, not a second answer)",
                 reply and accepted and reply["type"] == "RESULT" and reply["data"] == accepted["data"],
                 describe(reply))

    r = link.request("SUBMIT_ANSWER", {**q_ids, "attempt_id": new_id(), "choice_id": choice}, room)
    link.send(r)
    reply = link.reply(r)
    report.check("A6", "answer for an old/unknown attempt -> STALE_ATTEMPT", is_error(reply, "STALE_ATTEMPT", r),
                 describe(reply))

    closed = link.next("STATE", phase="RESULTS", timeout=question["time_limit_ms"] / 1000 + 5)
    ok, detail = False, "no STATE RESULTS"
    if closed:
        data = closed["data"]
        res = data["last_result"]
        own = res["own_result"] if res else None
        if own:
            correct = choice == res["correct_choice_id"]
            points_ok = own["points"] == 0 if not correct else 500 <= own["points"] <= 1000
            my_row = [row for row in data["leaderboard"]
                      if any(p["player_id"] == identity["actor_id"] for p in row["players"])]
            ok = (own["choice_id"] == choice and own["correct"] == correct and points_ok
                  and own["total_score"] == own["points"] == data["self"]["total_score"]
                  and my_row and my_row[0]["score"] == own["points"])
            detail = (f"{'correct' if correct else 'wrong'} answer, {own['points']} points, "
                      f"rank {my_row[0]['rank'] if my_row else '?'} of {len(data['leaderboard'])} rows")
    report.check("A7", "timer expires -> STATE RESULTS: correct answer revealed, points and leaderboard consistent",
                 ok, detail)

    r = link.request("SUBMIT_ANSWER", {**q_ids, "choice_id": choice}, room)
    link.send(r)
    reply = link.reply(r)
    report.check("A8", "answer after the question closed -> ANSWER_LATE", is_error(reply, "ANSWER_LATE", r),
                 describe(reply))

    time.sleep(0.5)
    stop_udp.set()
    udp_sock.close()
    check_udp(records, report, show_udp)

    report.check("M1", "every server message matched the catalogue (envelope, fields, RESULT, STATE rules)",
                 not link.problems, "; ".join(link.problems[:3]))
    link.close()

    # T4: duplicate username, compared case-insensitively.
    second = Link(args.server, args.tcp_port)
    r = second.request("JOIN_ROOM", {"pin": args.pin, "username": name.upper()})
    second.send(r)
    reply = second.reply(r)
    report.check("T4", f"second client joining as {name.upper()!r} (same name, other case) -> NAME_TAKEN",
                 is_error(reply, "NAME_TAKEN", r), describe(reply))
    second.close()


def check_udp(records, report: Report, show_table: bool) -> None:
    accepted = [r for r in records if r["verdict"] == udp.ACCEPTED]
    seqs = [r["seq"] for r in accepted]
    if show_table:
        say("      #   seq  remaining   bytes  verdict")
        for i, r in enumerate(records, 1):
            remaining = f"{r['remaining']:>6} ms" if r["remaining"] is not None else "       -"
            seq = r["seq"] if r["seq"] is not None else "-"
            say(f"    {i:>3}  {seq:>4}  {remaining}  {r['bytes']:>6}  {r['verdict']}")
    verdicts = {r["verdict"] for r in records}
    received = {r["seq"] for r in records if r["seq"] is not None}
    missing = sorted(set(range(1, max(seqs) + 1)) - received) if seqs else []
    report.check("U2", "accepted snapshots have strictly increasing seq",
                 seqs and all(b > a for a, b in zip(seqs, seqs[1:])), f"{len(accepted)} accepted of {len(records)} received")
    report.check("U3", "duplicate snapshot rejected", udp.DUPLICATE in verdicts)
    report.check("U4", "reordered (older) snapshot rejected, so the countdown cannot jump back", udp.OUT_OF_ORDER in verdicts)
    bad_kinds = [udp.WRONG_BINDING, udp.WRONG_ATTEMPT, udp.FUTURE_REVISION, udp.OVERSIZE, udp.MALFORMED]
    absent = [k for k in bad_kinds if k not in verdicts]
    report.check("U5", "bad datagrams rejected: wrong binding, stale attempt, future revision, oversize, malformed",
                 not absent, "not seen: " + ", ".join(absent) if absent else "")
    # The wrong-source copy comes from another server port. A firewall on this
    # device may block it before it reaches the program, so what is checked is
    # that no snapshot from any other address was ever accepted.
    seen = udp.WRONG_SOURCE in verdicts
    report.check("U5b", "no snapshot from an address other than the server's UDP port is accepted",
                 all(tuple(r["source"]) == tuple(accepted[0]["source"]) for r in accepted) if accepted else False,
                 "wrong-source copy arrived and was rejected" if seen
                 else "wrong-source copy never arrived (blocked by a firewall before the program)")
    report.check("U6", "lost snapshot tolerated: no retransmission, later snapshots still used",
                 missing and any(s > missing[0] for s in seqs), f"never arrived: seq {missing}")
    remaining = [r["remaining"] for r in accepted]
    report.check("U7", "countdown from accepted snapshots never goes up",
                 remaining and all(b <= a for a, b in zip(remaining, remaining[1:])),
                 f"{remaining[0]} ms -> {remaining[-1]} ms" if remaining else "")
    report.check("U8", "every accepted datagram is at most 512 bytes",
                 accepted and all(r["bytes"] <= udp.MAX_DATAGRAM for r in accepted),
                 f"largest accepted {max(r['bytes'] for r in accepted)} bytes" if accepted else "")


# --------------------------------------------------------------------------
# Fatal input: each on its own fresh connection
# --------------------------------------------------------------------------

def run_fatal_checks(args, report: Report) -> None:
    report.section("Fatal input   (only that connection is closed; the server keeps running)")

    def fresh_link_sending(data: bytes) -> Link:
        link = Link(args.server, args.tcp_port)
        link.send_raw(data)
        return link

    def frame_of(payload: bytes) -> bytes:
        return struct.pack("!I", len(payload)) + payload

    for cid, title, payload in (
            ("F1", "invalid JSON -> ERROR INVALID_MESSAGE (reply_to null), then closed", b'{"v":1,"type":'),
            ("F2", "duplicate JSON key -> ERROR INVALID_MESSAGE, then closed",
             b'{"v":1,"v":1,"type":"GET_STATE","request_id":"' + new_id().encode() + b'","data":{}}')):
        link = fresh_link_sending(frame_of(payload))
        reply = link.next("ERROR")
        closed = link.closed.wait(3)
        report.check(cid, title, is_error(reply, "INVALID_MESSAGE") and reply["reply_to"] is None and closed,
                     f"{describe(reply)}, {'closed' if closed else 'still open'}")
        link.close()

    request = {"v": 2, "type": "GET_STATE", "request_id": new_id(), "data": {}}
    link = fresh_link_sending(frame_of(encode_json(request)))
    reply = link.next("ERROR")
    closed = link.closed.wait(3)
    report.check("F3", "protocol version 2 -> ERROR UNSUPPORTED_VERSION (reply_to matches), then closed",
                 is_error(reply, "UNSUPPORTED_VERSION") and reply["reply_to"] == request["request_id"] and closed,
                 f"{describe(reply)}, {'closed' if closed else 'still open'}")
    link.close()

    for cid, title, header in (("F4", "frame length 0 -> closed without a reply", 0),
                               ("F5", "frame length 2,000,000 (> 1 MiB) -> closed at once, body never read", 2_000_000)):
        started = time.monotonic()
        link = fresh_link_sending(struct.pack("!I", header))
        closed = link.closed.wait(3)
        elapsed = time.monotonic() - started
        report.check(cid, title, closed and link.wait_for(lambda m: True, 0.1) is None,
                     f"closed after {elapsed:.2f} s" if closed else "still open")
        link.close()

    if args.skip_slow:
        report.section("  (F6 stalled-frame check skipped with --skip-slow)")
    else:
        if not report.quiet:
            say(f"  ....  F6   waiting up to {args.partial_timeout + 3:g} s for the stalled-frame timeout")
        started = time.monotonic()
        link = fresh_link_sending(struct.pack("!I", 100) + b'{"v":1')
        closed = link.closed.wait(args.partial_timeout + 3)
        elapsed = time.monotonic() - started
        reply = link.wait_for(lambda m: True, 0.1)
        report.check("F6", f"frame stalls after 10 of 104 bytes -> ERROR then closed after about "
                           f"{args.partial_timeout:g} s", closed and is_error(reply, "INVALID_MESSAGE")
                     and elapsed >= args.partial_timeout - 1, f"closed after {elapsed:.1f} s, {describe(reply)}")
        link.close()

    link = Link(args.server, args.tcp_port)
    r = link.request("JOIN_ROOM", {"pin": args.pin, "username": f"after-{random.randint(1000, 9999)}"})
    link.send(r)
    reply = link.reply(r)
    report.check("F7", "server still accepts and serves a new client after all of the above",
                 reply and reply["type"] == "RESULT", describe(reply))
    link.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat protocol check client.")
    parser.add_argument("--server", required=True, help="server IPv4 address printed by the server")
    parser.add_argument("--pin", required=True, help="test room PIN printed by the server")
    parser.add_argument("--name", default=f"tester-{random.randint(1000, 9999)}", help="username to join with")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT)
    parser.add_argument("--udp-port", type=int, default=UDP_PORT)
    parser.add_argument("--players", type=int, default=1, help="run this many players at the same time")
    parser.add_argument("--partial-timeout", type=float, default=10.0, help="must match the server's setting")
    parser.add_argument("--skip-slow", action="store_true", help="skip the 10-second stalled-frame check")
    args = parser.parse_args()
    args.server = socket.gethostbyname(args.server)   # one IPv4 address for both TCP and UDP

    say("Fat-Rat protocol check client")
    try:
        socket.create_connection((args.server, args.tcp_port), timeout=5).close()
    except OSError as exc:
        say(f"\nCannot reach the server at {args.server}:{args.tcp_port} ({exc}).\n"
            "Check that the server is running, both devices are on the same network, the IP address\n"
            "is the one the server printed, and the server computer's firewall allows Python.")
        sys.exit(2)
    reports = []
    if args.players == 1:
        reports.append(Report())
        run_player(args, args.name, reports[0], show_udp=True)
    else:
        say(f"\nRunning {args.players} players at the same time (each runs every T/V/U/A check)...")
        threads = []
        for i in range(args.players):
            report = Report(quiet=True)
            reports.append(report)
            thread = threading.Thread(target=run_player, args=(args, f"{args.name}-{i + 1}", report, False))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()
        for i, report in enumerate(reports, 1):
            passed = len(report.rows) - len(report.failed)
            say(f"  player {i}: {passed}/{len(report.rows)} checks passed"
                + "".join(f"\n      FAIL {cid} {title}  {detail}" for cid, title, _, detail in report.failed))
    fatal = Report()
    reports.append(fatal)
    run_fatal_checks(args, fatal)

    total = sum(len(r.rows) for r in reports)
    failed = sum(len(r.failed) for r in reports)
    say(f"\n{total - failed}/{total} checks passed" + ("" if failed else " - protocol check PASSED"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
