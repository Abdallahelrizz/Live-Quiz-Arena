"""Rooms check client: tests the room limits and several rooms at once.

Run it on a different device from the server, from the repository folder:

    python3 -m protocol_check.rooms_client --server 192.168.1.35

It starts simulated hosts and players (each with its own TCP connection, and
UDP where needed) and checks, printing PASS/FAIL:

  L  Largest allowed quiz (30 questions x 8 choices, 1000/300-character
     texts, 300 s timer, about 400 KB in one TCP frame) and the rejections
     just past each limit; 32- versus 33-character names.
  C  Maximum and limits, in one room: one unfinished room per host, quiz
     validation, the 5-player start gate, 50 players joining at the same
     time, the 51st rejected, a disconnected player keeping its slot, LEAVE
     freeing a slot, a race for the last slot, a full 50-player question,
     finishing, and the host creating a new room afterwards.
  R  Three rooms at the same time, each with its own host and 5 players:
     separate PINs, players, questions, answers, timers, live counts and
     scores, with no message from one room reaching another.

The report allows "testing scripts to simulate multiple players and rooms";
these bots are exactly that. Every message they receive is also validated
against the protocol catalogue.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from typing import Callable, List, Optional

from fatrat.protocol import udp
from fatrat.protocol.catalogue import UNATTACHED_REQUESTS
from fatrat.protocol.framing import encode_frame
from protocol_check.client import Link, Report, describe, is_error
from protocol_check.common import TCP_PORT, new_id, new_token, say


class Bot:
    """One simulated host or player."""

    def __init__(self, args, name: str, role: str = "PLAYER"):
        self.args, self.name, self.role = args, name, role
        self.identity: dict = {}
        self.state: Optional[dict] = None          # latest STATE received
        self.rooms_seen = set()
        self.live: Optional[udp.LiveStatusFilter] = None
        self.live_accepted: List[dict] = []
        self.udp_sock: Optional[socket.socket] = None
        self.left = False
        self.link = Link(args.server, args.tcp_port, self._on_message, role=role)

    def _on_message(self, m: dict) -> None:            # TCP reader thread
        if "room_id" in m:
            self.rooms_seen.add(m["room_id"])
        if m["type"] == "RESULT" and "session_id" in m["data"]:
            self.identity.update(m["data"])
        elif m["type"] == "UDP_BOUND" and self.live:
            self.live.confirm_binding(m["data"]["binding_id"])
        elif m["type"] == "STATE":
            self.state = m
            if self.live:
                data, q = m["data"], m["data"]["current_question"]
                if data["phase"] in ("OPEN", "PAUSED") and q:
                    self.live.set_context(room_id=m["room_id"], session_id=self.identity.get("session_id"),
                                          question_id=q["question_id"], attempt_id=q["attempt_id"],
                                          control_rev=data["control_rev"])
                else:
                    self.live.clear_context()

    @property
    def joined(self) -> bool:
        return "session_id" in self.identity

    def call(self, mtype: str, data: dict, timeout: float = 10.0) -> Optional[dict]:
        room = None if mtype in UNATTACHED_REQUESTS else self.identity.get("room_id")
        request = self.link.request(mtype, data, room)
        self.link.send(request)
        return self.link.reply(request, timeout)

    def host_call(self, mtype: str, **data) -> Optional[dict]:
        """Host command carrying the latest control_rev the host has seen."""
        return self.call(mtype, {"expected_control_rev": self.state["data"]["control_rev"], **data})

    def wait_state(self, condition: Callable[[dict], bool], timeout: float = 10.0) -> Optional[dict]:
        return self.link.wait_for(lambda m: m["type"] == "STATE" and condition(m["data"]), timeout)

    def enable_udp(self) -> bool:
        """REQUEST_UDP_BIND, then UDP_HELLO until UDP_BOUND (at most 5 tries, 1 s apart)."""
        bind = self.call("REQUEST_UDP_BIND", {})
        if not bind or bind["type"] != "RESULT":
            return False
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.bind(("0.0.0.0", 0))
        self.udp_sock.settimeout(0.3)
        server_udp = (self.args.server, bind["data"]["server_udp_port"])
        self.live = udp.LiveStatusFilter(server_udp)
        if self.state:
            self._on_message(self.state)               # load the current question context
        threading.Thread(target=self._udp_loop, daemon=True).start()
        hello = udp.encode_datagram({"v": 1, "type": "UDP_HELLO", "session_id": self.identity["session_id"],
                                     "binding_id": bind["data"]["binding_id"],
                                     "data": {"nonce": bind["data"]["udp_nonce"]}})
        for _ in range(5):
            self.udp_sock.sendto(hello, server_udp)
            if self.link.wait_for(lambda m: m["type"] == "UDP_BOUND", 1.0):
                return True
        return False

    def _udp_loop(self) -> None:
        while True:
            try:
                payload, source = self.udp_sock.recvfrom(udp.RECV_BUFFER)
            except socket.timeout:
                continue
            except OSError:
                return
            message, verdict = self.live.check(payload, source)
            if verdict == udp.ACCEPTED:
                self.live_accepted.append(message)

    def close(self) -> None:
        self.link.close()
        if self.udp_sock:
            self.udp_sock.close()


def in_parallel(fn: Callable, items: list) -> list:
    """Run fn(item) for every item at the same time (one thread each); results in order."""
    results = [None] * len(items)

    def run(i, item):
        results[i] = fn(item)

    threads = [threading.Thread(target=run, args=(i, item)) for i, item in enumerate(items)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def make_quiz(title: str, count: int, time_ms: int) -> list:
    questions = []
    for i in range(count):
        ids = [new_id() for _ in range(4)]
        questions.append({"question_id": new_id(), "text": f"{title}: question {i + 1}",
                          "choices": [{"choice_id": c, "text": f"Option {j + 1}"} for j, c in enumerate(ids)],
                          "correct_choice_id": ids[i % 4], "time_limit_ms": time_ms})
    return questions


def ok_result(m: Optional[dict]) -> bool:
    return bool(m) and m["type"] == "RESULT"


def own_result_ok(own: dict, choice: Optional[str], correct_id: str) -> bool:
    """Correct answers earn 500..1000 points, wrong or missing answers 0."""
    correct = choice is not None and choice == correct_id
    points_ok = 500 <= own["points"] <= 1000 if correct else own["points"] == 0
    return own["choice_id"] == choice and own["correct"] == correct and points_ok


# --------------------------------------------------------------------------
# L: largest allowed quiz and name, sent over the network
# --------------------------------------------------------------------------

def limits_check(args, report: Report) -> List[Bot]:
    report.section("Largest allowed quiz and names   (30 questions x 8 choices, longest texts, 300 s)")
    host = Bot(args, "limits-host", role="HOST")
    reply = host.call("CREATE_ROOM", {"username": "limits-host", "host_token": new_token()})
    if not ok_result(reply):
        report.check("L1", "create a room for the limits check", False, describe(reply))
        return [host]
    pin = reply["data"]["pin"]
    host.wait_state(lambda d: d["phase"] == "LOBBY")

    biggest = []
    for i in range(30):
        ids = [new_id() for _ in range(8)]
        biggest.append({"question_id": new_id(), "text": "🧀" * 1000,          # 1000 characters, 4000 bytes
                        "choices": [{"choice_id": c, "text": "🐀" * 300} for c in ids],
                        "correct_choice_id": ids[i % 8], "time_limit_ms": 300_000})
    title = "T" * 100
    request = host.link.request("PUT_QUIZ", {"expected_control_rev": host.state["data"]["control_rev"],
                                             "title": title, "questions": biggest}, host.identity["room_id"])
    frame_bytes = len(encode_frame(request))
    host.link.send(request)
    reply = host.link.reply(request, timeout=15)
    echoed = host.wait_state(lambda d: d["quiz"] is not None, timeout=15)
    intact = echoed and echoed["data"]["quiz"] == {"title": title, "questions": biggest}
    report.check("L1", "largest allowed quiz -> RESULT, and the host's STATE carries it back byte-for-byte",
                 ok_result(reply) and intact,
                 f"PUT_QUIZ frame {frame_bytes:,} bytes; 30 questions, 8 choices each, 100-character title")

    def quiz_error(questions, title="Quiz"):
        return host.host_call("PUT_QUIZ", title=title, questions=questions)

    reply = quiz_error(make_quiz("q", 31, 30_000))
    report.check("L2", "31 questions -> INVALID_QUIZ", is_error(reply, "INVALID_QUIZ"), describe(reply))
    reply = quiz_error([])
    report.check("L3", "0 questions -> INVALID_QUIZ", is_error(reply, "INVALID_QUIZ"), describe(reply))
    too_long = make_quiz("q", 1, 300_001)
    reply = quiz_error(too_long)
    report.check("L4", "timer of 300,001 ms (over 300 s) -> INVALID_QUIZ", is_error(reply, "INVALID_QUIZ"),
                 describe(reply))
    reply = quiz_error(make_quiz("q", 1, 30_000), title="T" * 101)
    report.check("L5", "101-character title -> INVALID_QUIZ", is_error(reply, "INVALID_QUIZ"), describe(reply))

    players = [Bot(args, "x"), Bot(args, "x")]
    long_ok = players[0].call("JOIN_ROOM", {"pin": pin, "username": "ج" * 32})
    long_bad = players[1].call("JOIN_ROOM", {"pin": pin, "username": "ج" * 33})
    report.check("L6", "32-character name accepted, 33-character name -> INVALID_USERNAME",
                 ok_result(long_ok) and is_error(long_bad, "INVALID_USERNAME"),
                 f"{describe(long_ok)} / {describe(long_bad)}")
    return [host] + players


# --------------------------------------------------------------------------
# C: the maximum and the other room limits
# --------------------------------------------------------------------------

def capacity_check(args, report: Report) -> List[Bot]:
    report.section("Maximum players and room limits   (one room, host + up to 50 players)")
    bots: List[Bot] = []
    token = new_token()
    host = Bot(args, "cap-host", role="HOST")
    bots.append(host)
    reply = host.call("CREATE_ROOM", {"username": "cap-host", "host_token": token})
    lobby = host.wait_state(lambda d: d["phase"] == "LOBBY")
    report.check("C1", "CREATE_ROOM -> RESULT (role HOST, new PIN) + host STATE LOBBY",
                 ok_result(reply) and reply["data"]["role"] == "HOST" and lobby,
                 f"room PIN {reply['data']['pin']}" if ok_result(reply) else describe(reply))
    if not ok_result(reply):
        return bots
    pin = reply["data"]["pin"]

    other = Bot(args, "cap-host", role="HOST")
    bots.append(other)
    second = other.call("CREATE_ROOM", {"username": "cap-host", "host_token": token})
    report.check("C2", "same saved host_token creates another room while the first is unfinished -> HOST_HAS_ROOM",
                 is_error(second, "HOST_HAS_ROOM") and second["data"]["existing_room_id"] == host.identity["room_id"],
                 describe(second))
    other.close()

    reply = host.host_call("START_QUIZ")
    report.check("C3", "START_QUIZ before a quiz is loaded -> INVALID_STATE", is_error(reply, "INVALID_STATE"),
                 describe(reply))
    bad = make_quiz("bad", 1, 30_000)
    bad[0]["choices"] += [{"choice_id": new_id(), "text": f"extra {i}"} for i in range(5)]   # 9 choices
    reply = host.host_call("PUT_QUIZ", title="Too many choices", questions=bad)
    report.check("C4", "PUT_QUIZ with a 9-choice question -> INVALID_QUIZ", is_error(reply, "INVALID_QUIZ"),
                 describe(reply))
    quiz = make_quiz("Capacity", 1, 30_000)
    reply = host.host_call("PUT_QUIZ", title="Capacity test", questions=quiz)
    host.wait_state(lambda d: d["quiz"] is not None)
    report.check("C5", "PUT_QUIZ with a valid quiz -> RESULT", ok_result(reply), describe(reply))

    players = [Bot(args, f"cap-{i + 1:02d}") for i in range(4)]
    bots += players
    joined = in_parallel(lambda b: b.call("JOIN_ROOM", {"pin": pin, "username": b.name}), players)
    host.wait_state(lambda d: len(d["participants"]) == 4)
    reply = host.host_call("START_QUIZ")
    report.check("C6", "START_QUIZ with 4 connected players -> NOT_ENOUGH_PLAYERS (5 needed)",
                 all(ok_result(j) for j in joined) and is_error(reply, "NOT_ENOUGH_PLAYERS"), describe(reply))

    more = [Bot(args, f"cap-{i + 1:02d}") for i in range(4, 50)]
    bots += more
    started = time.monotonic()
    joined = in_parallel(lambda b: b.call("JOIN_ROOM", {"pin": pin, "username": b.name}), more)
    elapsed = time.monotonic() - started
    players += more
    full = host.wait_state(lambda d: len(d["participants"]) == 50)
    report.check("C7", f"{len(more)} players join at the same time -> all accepted; room has 50 players",
                 all(ok_result(j) for j in joined) and full,
                 f"{sum(ok_result(j) for j in joined)}/{len(more)} joined in {elapsed:.2f} s")

    extra = Bot(args, "cap-51")
    bots.append(extra)
    reply = extra.call("JOIN_ROOM", {"pin": pin, "username": "cap-51"})
    report.check("C8", "51st player -> ROOM_FULL", is_error(reply, "ROOM_FULL"), describe(reply))

    players[0].close()                                    # disconnect without LEAVE_ROOM
    host.wait_state(lambda d: any(p["username"] == players[0].name and not p["connected"]
                                  for p in d["participants"]))
    reply = extra.call("JOIN_ROOM", {"pin": pin, "username": "cap-51"})
    report.check("C9", "a player disconnects (no LEAVE) -> still full, its slot is kept -> ROOM_FULL",
                 is_error(reply, "ROOM_FULL"), describe(reply))

    reply = players[1].call("LEAVE_ROOM", {})
    closed = players[1].link.closed.wait(3)
    players[1].left = True
    report.check("C10", "LEAVE_ROOM -> RESULT {left: true}, then the server closes that connection",
                 ok_result(reply) and reply["data"] == {"left": True} and closed, describe(reply))
    again = Bot(args, players[1].name)
    bots.append(again)
    reply = again.call("JOIN_ROOM", {"pin": pin, "username": players[1].name})
    report.check("C11", "joining with the name of the player who left -> NAME_TAKEN (name stays reserved)",
                 is_error(reply, "NAME_TAKEN"), describe(reply))
    reply = extra.call("JOIN_ROOM", {"pin": pin, "username": "cap-51"})
    report.check("C12", "the slot freed by LEAVE can be used -> new player accepted", ok_result(reply), describe(reply))
    players.append(extra)

    players[2].call("LEAVE_ROOM", {})
    players[2].left = True
    racers = [Bot(args, f"cap-race-{i + 1}") for i in range(3)]
    bots += racers
    outcomes = in_parallel(lambda b: b.call("JOIN_ROOM", {"pin": pin, "username": b.name}), racers)
    winners = [b for b, o in zip(racers, outcomes) if ok_result(o)]
    report.check("C13", "3 players race for the last free slot at the same time -> exactly 1 accepted, 2 ROOM_FULL",
                 len(winners) == 1 and sum(is_error(o, "ROOM_FULL") for o in outcomes) == 2,
                 ", ".join(describe(o) for o in outcomes))
    players += winners
    full = host.wait_state(lambda d: sum(p["participation"] == "ACTIVE" for p in d["participants"]) == 50)

    # Play one question with the full room.
    connected = [b for b in players if b.joined and not b.left and b is not players[0]]
    started = time.monotonic()
    reply = host.host_call("START_QUIZ")
    opened = in_parallel(lambda b: b.wait_state(lambda d: d["phase"] == "OPEN"), connected)
    elapsed = time.monotonic() - started
    report.check("C14", "START_QUIZ with a full room -> every connected player gets STATE OPEN",
                 ok_result(reply) and all(opened),
                 f"{sum(1 for o in opened if o)}/{len(connected)} connected players within {elapsed * 1000:.0f} ms")
    question = quiz[0]
    choices = [c["choice_id"] for c in question["choices"]]
    picks = {b.name: choices[i % 4] for i, b in enumerate(connected)}
    q = opened[0]["data"]["current_question"] if opened[0] else {}
    started = time.monotonic()
    answers = in_parallel(lambda b: b.call("SUBMIT_ANSWER", {
        "question_id": q.get("question_id"), "attempt_id": q.get("attempt_id"), "choice_id": picks[b.name]}),
        connected)
    elapsed = time.monotonic() - started
    report.check("C15", f"{len(connected)} players answer at the same time -> every answer accepted exactly once",
                 all(ok_result(a) for a in answers),
                 f"{sum(ok_result(a) for a in answers)}/{len(connected)} accepted in {elapsed * 1000:.0f} ms")
    reply = host.call("GET_STATE", {})
    snap = host.wait_state(lambda d: d["live"] is not None)
    live = snap["data"]["live"] if snap else None
    report.check("C16", "the disconnected player still counts as eligible, so the question stays OPEN",
                 snap and snap["data"]["phase"] == "OPEN" and live == {"answered_count": len(connected),
                                                                      "eligible_count": 50},
                 f"live counts {live}")

    host.host_call("CLOSE_QUESTION")
    results = in_parallel(lambda b: b.wait_state(lambda d: d["phase"] == "RESULTS"), connected)
    good = [r for b, r in zip(connected, results)
            if r and own_result_ok(r["data"]["last_result"]["own_result"], picks[b.name], question["correct_choice_id"])]
    report.check("C17", "host CLOSE_QUESTION -> every player gets STATE RESULTS with a correct own result",
                 len(good) == len(connected), f"{len(good)}/{len(connected)} consistent, reason HOST_CLOSED")
    final = host.wait_state(lambda d: d["phase"] == "RESULTS")
    if final:
        listed = [p for row in final["data"]["leaderboard"] for p in row["players"]]
        left = [p for p in listed if p["participation"] == "LEFT"]
        rows = len(final["data"]["leaderboard"])
    report.check("C18", "leaderboard lists all 52 participants (50 active + 2 who left, labelled LEFT), ties grouped",
                 final and len(listed) == 52 and len(left) == 2,
                 f"{len(listed)} players on {rows} rank rows, {len(left)} LEFT" if final else "")

    reply = host.host_call("NEXT_QUESTION")
    finished = in_parallel(lambda b: b.wait_state(lambda d: d["phase"] == "FINISHED"), connected + [host])
    report.check("C19", "NEXT_QUESTION after the last question -> FINISHED (COMPLETED) for everyone",
                 ok_result(reply) and all(f and f["data"]["finish_reason"] == "COMPLETED" for f in finished),
                 f"{sum(1 for f in finished if f)}/{len(finished)} received FINISHED")
    late = Bot(args, "cap-late")
    bots.append(late)
    reply = late.call("JOIN_ROOM", {"pin": pin, "username": "cap-late"})
    report.check("C20", "joining a finished room -> ROOM_FINISHED", is_error(reply, "ROOM_FINISHED"), describe(reply))
    again = Bot(args, "cap-host", role="HOST")
    bots.append(again)
    reply = again.call("CREATE_ROOM", {"username": "cap-host", "host_token": token})
    report.check("C21", "after finishing, the same host_token can create a new room",
                 ok_result(reply) and reply["data"]["pin"] != pin, describe(reply))
    return bots


# --------------------------------------------------------------------------
# R: three rooms at the same time
# --------------------------------------------------------------------------

def rooms_check(args, report: Report, room_count: int = 3, per_room: int = 5) -> List[Bot]:
    report.section(f"{room_count} rooms at the same time   (separate hosts, {per_room} players each)")
    time_ms = int(args.question_seconds * 1000)
    hosts = [Bot(args, f"host-{k + 1}", role="HOST") for k in range(room_count)]
    created = in_parallel(lambda h: h.call("CREATE_ROOM", {"username": h.name, "host_token": new_token()}), hosts)
    pins = [c["data"]["pin"] for c in created if ok_result(c)]
    report.check("R1", f"{room_count} hosts create rooms at the same time -> {room_count} different PINs",
                 len(set(pins)) == room_count, "PINs " + ", ".join(pins))
    if len(set(pins)) != room_count:
        return hosts
    for h in hosts:
        h.wait_state(lambda d: d["phase"] == "LOBBY")
    quizzes = [make_quiz(f"Room {k + 1}", 2, time_ms) for k in range(room_count)]
    loaded = [h.host_call("PUT_QUIZ", title=f"Room {k + 1} quiz", questions=quizzes[k]) for k, h in enumerate(hosts)]
    report.check("R2", "each host loads its own 2-question quiz", all(ok_result(r) for r in loaded))

    rooms = [[Bot(args, "rat" if i == 0 else f"r{k + 1}-p{i + 1}") for i in range(per_room)] for k in range(room_count)]
    everyone = [(k, b) for k, room in enumerate(rooms) for b in room]
    joined = in_parallel(lambda kb: kb[1].call("JOIN_ROOM", {"pin": pins[kb[0]], "username": kb[1].name}), everyone)
    report.check("R3", f"{len(everyone)} players join their rooms at the same time -> all accepted",
                 all(ok_result(j) for j in joined), f"{sum(ok_result(j) for j in joined)}/{len(everyone)}")
    report.check("R4", "the same name 'rat' is accepted once in every room (names are per room)",
                 all(ok_result(joined[k * per_room]) for k in range(room_count)))
    bound = in_parallel(lambda kb: kb[1].enable_udp(), everyone)
    report.check("R5", f"all {len(everyone)} players register UDP (REQUEST_UDP_BIND, UDP_HELLO, UDP_BOUND)",
                 all(bound), f"{sum(bound)}/{len(everyone)}")
    for k, h in enumerate(hosts):
        h.wait_state(lambda d: len(d["participants"]) == per_room)

    # Question 1: everyone answers; room k has k + 2 correct answers.
    started = in_parallel(lambda h: h.host_call("START_QUIZ"), hosts)
    opened = in_parallel(lambda kb: kb[1].wait_state(lambda d: d["phase"] == "OPEN" and d["question_index"] == 0),
                         everyone)
    attempts = {o["data"]["current_question"]["attempt_id"] for o in opened if o}
    report.check("R6", "hosts start together -> each player gets its own room's question; attempts differ per room",
                 all(ok_result(s) for s in started) and all(
                     o and o["data"]["current_question"]["question_id"] == quizzes[k][0]["question_id"]
                     for (k, _), o in zip(everyone, opened)) and len(attempts) == room_count)
    time.sleep(1.5)                                          # let LIVE_STATUS snapshots arrive

    def pick(k, i, qi):
        q = quizzes[k][qi]
        wrong = [c["choice_id"] for c in q["choices"] if c["choice_id"] != q["correct_choice_id"]][0]
        return q["correct_choice_id"] if i < k + 2 else wrong

    picks = {}
    for k, room in enumerate(rooms):
        for i, b in enumerate(room):
            picks[(b, 0)] = pick(k, i, 0)

    def answer(kb, qi):
        k, b = kb
        q = b.state["data"]["current_question"]
        return b.call("SUBMIT_ANSWER", {"question_id": q["question_id"], "attempt_id": q["attempt_id"],
                                        "choice_id": picks[(b, qi)]})

    in_parallel(lambda kb: answer(kb, 0), everyone)
    results = in_parallel(lambda kb: kb[1].wait_state(
        lambda d: d["phase"] == "RESULTS" and d["question_index"] == 0), everyone)
    consistent = all(r and r["data"]["last_result"]["reason"] == "ALL_ANSWERED" and own_result_ok(
        r["data"]["last_result"]["own_result"], picks[(b, 0)], quizzes[k][0]["correct_choice_id"])
        for (k, b), r in zip(everyone, results))
    correct_counts = [sum(1 for b in room if picks[(b, 0)] == quizzes[k][0]["correct_choice_id"])
                      for k, room in enumerate(rooms)]
    report.check("R7", "everyone answers -> each room closes early (ALL_ANSWERED) with each player's own result",
                 consistent, "correct answers per room: " + ", ".join(map(str, correct_counts)))
    live_ok = all(b.live_accepted and all(s["data"]["eligible_count"] == per_room for s in b.live_accepted)
                  for _, b in everyone)
    report.check("R8", f"every player received LIVE_STATUS over UDP counting only its own room ({per_room} eligible)",
                 live_ok, f"{sum(len(b.live_accepted) for _, b in everyone)} snapshots accepted in total")

    # Question 2: room 1 does not answer (timer), rooms 2.. answer at once (all answered).
    for k, room in enumerate(rooms):
        for i, b in enumerate(room):
            picks[(b, 1)] = quizzes[k][1]["correct_choice_id"]
    for k, h in enumerate(hosts):
        h.wait_state(lambda d: d["phase"] == "RESULTS")
    t0 = time.monotonic()
    in_parallel(lambda h: h.host_call("NEXT_QUESTION"), hosts)
    for _, b in everyone:
        b.wait_state(lambda d: d["phase"] == "OPEN" and d["question_index"] == 1)
    answering = [(k, b) for k, b in everyone if k != 0]
    in_parallel(lambda kb: answer(kb, 1), answering)
    closes = {}

    def wait_close(kb):
        r = kb[1].wait_state(lambda d: d["phase"] == "RESULTS" and d["question_index"] == 1,
                             timeout=args.question_seconds + 5)
        return r, time.monotonic() - t0

    for (k, b), (r, when) in zip(everyone, in_parallel(wait_close, everyone)):
        if r:
            closes.setdefault(k, []).append((r["data"]["last_result"]["reason"], when))
    room1 = closes.get(0, [])
    others = [c for k in range(1, room_count) for c in closes.get(k, [])]
    timers_ok = (len(room1) == per_room and all(reason == "TIMEOUT" and when >= args.question_seconds - 1
                                                for reason, when in room1)
                 and len(others) == per_room * (room_count - 1)
                 and all(reason == "ALL_ANSWERED" and when < args.question_seconds - 1 for reason, when in others))
    report.check("R9", "separate timers: room 1 waits for its own timeout while the other rooms close at once",
                 timers_ok, f"room 1 closed after {max((w for _, w in room1), default=0):.1f} s (TIMEOUT), "
                            f"others after {max((w for _, w in others), default=0):.1f} s (ALL_ANSWERED)")

    in_parallel(lambda h: h.host_call("NEXT_QUESTION"), hosts)
    finals = in_parallel(lambda kb: kb[1].wait_state(lambda d: d["phase"] == "FINISHED"), everyone)
    own_ids = [{b.identity.get("actor_id") for b in room} for room in rooms]
    separate = all(f and {p["player_id"] for row in f["data"]["leaderboard"] for p in row["players"]} == own_ids[k]
                   for (k, _), f in zip(everyone, finals))
    report.check("R10", "final leaderboards: each room ranks exactly its own 5 players", separate)

    def expected_total(k, b):
        r1 = [r for (kk, bb), r in zip(everyone, results) if bb is b][0]
        return r1["data"]["last_result"]["own_result"]["points"]

    totals_ok = all(f and f["data"]["self"]["total_score"] >= expected_total(k, b)
                    and (k == 0) == (f["data"]["self"]["total_score"] == expected_total(k, b))
                    for (k, b), f in zip(everyone, finals))
    report.check("R11", "final scores add up per room (room 1 scored nothing on question 2, rooms 2-3 did)", totals_ok)
    report.check("R12", "no player or host ever received a message for another room",
                 all(len(b.rooms_seen) == 1 for _, b in everyone) and all(len(h.rooms_seen) == 1 for h in hosts))
    return hosts + [b for _, b in everyone]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat rooms check: maximum players and several rooms.")
    parser.add_argument("--server", required=True, help="server IPv4 address printed by the server")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT)
    parser.add_argument("--question-seconds", type=float, default=6.0, help="question time in the rooms check")
    parser.add_argument("--skip-capacity", action="store_true")
    parser.add_argument("--skip-rooms", action="store_true")
    args = parser.parse_args()
    args.server = socket.gethostbyname(args.server)
    say("Fat-Rat rooms check client")
    try:
        socket.create_connection((args.server, args.tcp_port), timeout=5).close()
    except OSError as exc:
        say(f"\nCannot reach the server at {args.server}:{args.tcp_port} ({exc}).")
        sys.exit(2)

    report = Report()
    bots: List[Bot] = limits_check(args, report)
    for b in bots:
        b.close()
    if not args.skip_capacity:
        bots += capacity_check(args, report)
        for b in bots:
            b.close()
    if not args.skip_rooms:
        room_bots = rooms_check(args, report)
        bots += room_bots
        for b in room_bots:
            b.close()
    problems = [f"{b.name}: {p}" for b in bots for p in b.link.problems]
    report.check("M2", f"every message received by all {len(bots)} simulated clients matched the catalogue",
                 not problems, "; ".join(problems[:3]))
    failed = len(report.failed)
    say(f"\n{len(report.rows) - failed}/{len(report.rows)} checks passed" + ("" if failed else " - rooms check PASSED"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
