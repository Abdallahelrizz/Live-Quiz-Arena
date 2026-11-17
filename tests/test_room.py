"""Tests of the game server's room engine, registry and scoring, without sockets.

A fake connection records what the server would send, and a fake clock
replaces the real one, so every rule can be tested in milliseconds and
deadlines can be reached exactly. Every message the server queues is also
validated against the protocol catalogue, as a real client would.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from fatrat.protocol import udp
from fatrat.protocol.catalogue import TCP
from fatrat.protocol.framing import decode_json
from fatrat.protocol.messages import validate_message, validate_state
from fatrat.server import registry as registry_module
from fatrat.server.registry import Registry
from fatrat.server.scoring import leaderboard, score
from tests.samples import new_id, new_token, question


class FakeConn:
    """Stands in for server.connection.Connection: records instead of sending."""

    def __init__(self, ip: str = "10.0.0.2"):
        self.peer = (ip, 50000)
        self.who = ip
        self.member = None
        self.sent = []
        self.closed = False

    def send(self, message: dict) -> None:
        assert not self.closed, "nothing may be queued after close()"
        validate_message(message, transport=TCP, from_server=True, strict=True)
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True

    def last(self, mtype: str) -> dict:
        return next(m for m in reversed(self.sent) if m["type"] == mtype)

    def state(self) -> dict:
        message = self.last("STATE")
        validate_state(message, role=message["data"]["self"]["role"])
        return message["data"]


class FakeUdp:
    def __init__(self):
        self.bindings = {}

    def register(self, binding_id, member):
        self.bindings[binding_id] = member

    def forget(self, binding_id):
        self.bindings.pop(binding_id, None)


class Clock:
    def __init__(self):
        self.ms = 1_000_000

    def __call__(self) -> int:
        return self.ms


class Game:
    """A room with a host and players, driven through the same calls the server makes."""

    def __init__(self, test: unittest.TestCase, players: int = 5, questions=None):
        self.clock = Clock()
        quiet = lambda *parts: None                     # keep the test output free of server log lines
        for target, fake in (("fatrat.server.room.now_ms", self.clock),
                             ("fatrat.server.projection.now_ms", self.clock),
                             ("fatrat.server.room.log", quiet), ("fatrat.server.registry.log", quiet)):
            patcher = mock.patch(target, fake)
            patcher.start()
            test.addCleanup(patcher.stop)
        self.udp = FakeUdp()
        self.registry = Registry(self.udp, start_timer=lambda room: None)
        self.host_token = new_token()
        self.host = FakeConn("10.0.0.1")
        self.registry.create(self.host, self.request("CREATE_ROOM", username="Teacher",
                                                     host_token=self.host_token))
        self.room = self.host.member.room
        self.players = [self.join(f"p{i + 1}") for i in range(players)]
        self.questions = questions if questions is not None else [question(time_limit_ms=10_000),
                                                                  question(time_limit_ms=10_000)]
        if self.questions:
            self.cmd("PUT_QUIZ", title="Networks", questions=self.questions)

    def request(self, mtype: str, room: bool = False, rid: str = None, **data) -> dict:
        message = {"v": 1, "type": mtype, "request_id": rid or new_id(), "data": data}
        if room:
            message["room_id"] = self.room.room_id
        return message

    def join(self, name: str, conn: FakeConn = None) -> FakeConn:
        conn = conn or FakeConn()
        self.room.join(conn, self.request("JOIN_ROOM", pin=self.room.pin, username=name))
        return conn

    def send(self, conn: FakeConn, mtype: str, rid: str = None, **data) -> dict:
        """Send a request as `conn` and return its RESULT or ERROR."""
        message = self.request(mtype, room=True, rid=rid, **data)
        self.registry.handle(conn, self.room, message)
        return next(m for m in reversed(conn.sent) if m.get("reply_to") == message["request_id"])

    def cmd(self, mtype: str, rev: int = None, **data) -> dict:
        """A host command carrying the current control_rev unless told otherwise."""
        rev = self.room.control_rev if rev is None else rev
        return self.send(self.host, mtype, expected_control_rev=rev, **data)

    def answer(self, conn: FakeConn, choice: int = 0, rid: str = None) -> dict:
        q = self.room.current
        return self.send(conn, "SUBMIT_ANSWER", rid=rid, question_id=q["question_id"],
                         attempt_id=self.room.attempt_id, choice_id=q["choices"][choice]["choice_id"])

    def resume(self, token: str) -> FakeConn:
        conn = FakeConn()
        self.room.resume(conn, self.request("RESUME_SESSION", room_id=self.room.room_id,
                                            reconnect_token=token))
        return conn

    def token(self, conn: FakeConn) -> str:
        return conn.last("RESULT")["data"]["reconnect_token"] if conn.member is None else conn.member.token


def code(reply: dict) -> str:
    return reply["data"].get("code", "RESULT") if reply["type"] == "ERROR" else "RESULT"


class ScoringTests(unittest.TestCase):
    def test_score_range_and_rounding(self):
        self.assertEqual(score(10_000, 10_000), 1000)      # instant answer
        self.assertEqual(score(0, 10_000), 500)            # last moment
        self.assertEqual(score(5_000, 10_000), 750)
        self.assertEqual(score(1, 1_000), 501)             # 500.5 rounds half up
        self.assertEqual(score(99_999, 10_000), 1000)      # clamped to the limit
        self.assertEqual(score(-5, 10_000), 500)

    def test_leaderboard_dense_ranks_and_ties(self):
        rows = leaderboard([(900, "Zed", "z", "ACTIVE"), (900, "amy", "a", "LEFT"),
                            (500, "Bob", "b", "ACTIVE"), (0, "Cat", "c", "REMOVED")])
        self.assertEqual([(r["rank"], r["score"]) for r in rows], [(1, 900), (2, 500), (3, 0)])
        self.assertEqual([p["username"] for p in rows[0]["players"]], ["amy", "Zed"])
        self.assertEqual(rows[0]["players"][0]["participation"], "LEFT")


class RoomCreationAndJoinTests(unittest.TestCase):
    def test_one_unfinished_room_per_host(self):
        g = Game(self, players=0)
        again = FakeConn()
        g.registry.create(again, g.request("CREATE_ROOM", username="Teacher", host_token=g.host_token))
        reply = again.last("ERROR")
        self.assertEqual(reply["data"]["code"], "HOST_HAS_ROOM")
        self.assertEqual(reply["data"]["existing_room_id"], g.room.room_id)
        self.assertEqual(g.registry.room_count(), 1)

    def test_repeated_create_request_does_not_create_a_second_room(self):
        g = Game(self, players=0, questions=[])
        rid = new_id()
        first, retry, changed = FakeConn(), FakeConn(), FakeConn()
        token = new_token()
        g.registry.create(first, g.request("CREATE_ROOM", rid=rid, username="A", host_token=token))
        g.registry.create(retry, g.request("CREATE_ROOM", rid=rid, username="A", host_token=token))
        g.registry.create(changed, g.request("CREATE_ROOM", rid=rid, username="B", host_token=token))
        self.assertEqual(retry.last("ERROR")["data"]["code"], "ROOM_ALREADY_CREATED")
        self.assertEqual(changed.last("ERROR")["data"]["code"], "REQUEST_ID_REUSED")
        self.assertEqual(g.registry.room_count(), 2)

    def test_simultaneous_creates_with_one_saved_identity_make_one_room(self):
        # Eight threads send CREATE_ROOM for the same host at the same moment,
        # each with its own request ID. The registry lock makes check-and-create
        # one step, so exactly one wins and the others are told which room it is.
        # Building a room is slowed down on purpose: that widens the gap between
        # "this host has no room yet" and "room saved", so if the lock were
        # missing, several threads would almost surely slip through the gap.
        g = Game(self, players=0, questions=[])
        build = registry_module.Room

        def slow_room(*args, **kwargs):
            time.sleep(0.002)
            return build(*args, **kwargs)

        patcher = mock.patch("fatrat.server.registry.Room", slow_room)
        patcher.start()
        self.addCleanup(patcher.stop)
        for _ in range(20):
            token, conns = new_token(), [FakeConn() for _ in range(8)]
            gate = threading.Barrier(len(conns))

            def create(conn):
                gate.wait()
                g.registry.create(conn, g.request("CREATE_ROOM", username="Teacher", host_token=token))

            threads = [threading.Thread(target=create, args=(c,)) for c in conns]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = [c for c in conns if c.sent[0]["type"] == "RESULT"]
            self.assertEqual(len(winners), 1)
            room_id = winners[0].sent[0]["data"]["room_id"]
            for c in conns:
                if c is not winners[0]:
                    self.assertEqual(c.last("ERROR")["data"]["code"], "HOST_HAS_ROOM")
                    self.assertEqual(c.last("ERROR")["data"]["existing_room_id"], room_id)
        self.assertEqual(g.registry.room_count(), 21)

    def test_retry_after_finish_and_a_late_close_of_the_old_room(self):
        g = Game(self, players=0, questions=[])
        token, rid = new_token(), new_id()
        old_host = FakeConn()
        g.registry.create(old_host, g.request("CREATE_ROOM", rid=rid, username="T", host_token=token))
        old_room = old_host.member.room
        old_rev = old_room.control_rev
        g.registry.handle(old_host, old_room, {**g.request("CLOSE_ROOM", expected_control_rev=old_rev),
                                               "room_id": old_room.room_id})
        retry = FakeConn()                                  # the very first request again, after the finish
        g.registry.create(retry, g.request("CREATE_ROOM", rid=rid, username="T", host_token=token))
        self.assertEqual(retry.last("ERROR")["data"]["code"], "ROOM_ALREADY_CREATED")
        self.assertEqual(retry.last("ERROR")["data"]["existing_room_id"], old_room.room_id)
        new_host = FakeConn()
        g.registry.create(new_host, g.request("CREATE_ROOM", username="T", host_token=token))
        new_room = new_host.member.room
        # A late CLOSE_ROOM on the old, finished room is a no-op and must not
        # free the slot that now belongs to the new room.
        g.registry.handle(old_host, old_room, {**g.request("CLOSE_ROOM", expected_control_rev=old_room.control_rev),
                                               "room_id": old_room.room_id})
        third = FakeConn()
        g.registry.create(third, g.request("CREATE_ROOM", username="T", host_token=token))
        self.assertEqual(third.last("ERROR")["data"]["code"], "HOST_HAS_ROOM")
        self.assertEqual(third.last("ERROR")["data"]["existing_room_id"], new_room.room_id)

    def test_join_rejections(self):
        g = Game(self, players=1)
        self.assertEqual(g.join("P1").last("ERROR")["data"]["code"], "NAME_TAKEN")     # case-insensitive
        self.assertEqual(g.join("   ").last("ERROR")["data"]["code"], "INVALID_USERNAME")
        self.assertEqual(g.join("x" * 33).last("ERROR")["data"]["code"], "INVALID_USERNAME")
        self.assertIsNotNone(g.join("x" * 32).member)

    def test_fifty_player_cap_and_slot_reuse(self):
        g = Game(self, players=50)
        self.assertEqual(g.join("late").last("ERROR")["data"]["code"], "ROOM_FULL")
        g.room.disconnected(g.players[0])                   # disconnect keeps the slot
        self.assertEqual(g.join("late").last("ERROR")["data"]["code"], "ROOM_FULL")
        self.assertEqual(code(g.send(g.players[1], "LEAVE_ROOM")), "RESULT")
        self.assertTrue(g.players[1].closed)
        self.assertIsNotNone(g.join("late").member)         # leaving freed one slot
        self.assertEqual(g.join("p2").last("ERROR")["data"]["code"], "NAME_TAKEN")    # name stays reserved

    def test_resume_in_a_full_room_uses_the_kept_slot(self):
        g = Game(self, players=50)
        gone = g.players[7]
        token, actor = gone.member.token, gone.member.actor_id
        g.room.disconnected(gone)
        self.assertEqual(g.join("late").last("ERROR")["data"]["code"], "ROOM_FULL")
        back = g.resume(token)                              # 50 of 50: resuming takes no new slot
        self.assertEqual(back.last("RESULT")["data"]["actor_id"], actor)
        self.assertEqual(len(g.room.active_players()), 50)
        self.assertEqual(g.join("later").last("ERROR")["data"]["code"], "ROOM_FULL")

    def test_start_needs_a_quiz_and_five_connected_players(self):
        g = Game(self, players=5, questions=[])
        self.assertEqual(code(g.cmd("START_QUIZ")), "INVALID_STATE")
        g.cmd("PUT_QUIZ", title="Q", questions=[question()])
        g.room.disconnected(g.players[0])
        self.assertEqual(code(g.cmd("START_QUIZ")), "NOT_ENOUGH_PLAYERS")
        g.join("sixth")
        self.assertEqual(code(g.cmd("START_QUIZ")), "RESULT")
        self.assertEqual(g.host.state()["phase"], "OPEN")

    def test_invalid_quiz_is_refused_without_change(self):
        g = Game(self, questions=[])
        self.assertEqual(code(g.cmd("PUT_QUIZ", title="Q", questions=[question(choices=9)])), "INVALID_QUIZ")
        self.assertIsNone(g.room.quiz)
        self.assertEqual(g.room.control_rev, 0)


class QuestionTests(unittest.TestCase):
    def test_player_never_receives_text_or_the_correct_answer_while_open(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        player, host = g.players[0].state(), g.host.state()
        self.assertNotIn("text", player["current_question"])
        self.assertNotIn("text", player["current_question"]["choices"][0])
        self.assertNotIn("quiz", player)
        self.assertNotIn("correct_choice_id", str(g.players[0].last("STATE")))
        self.assertEqual(host["current_question"]["text"], g.questions[0]["text"])
        self.assertIsNone(player["last_result"])

    def test_answer_rules(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        p = g.players[0]
        q = g.room.current
        bad = g.send(p, "SUBMIT_ANSWER", question_id=q["question_id"], attempt_id=g.room.attempt_id,
                     choice_id=new_id())
        self.assertEqual(code(bad), "INVALID_CHOICE")
        stale = g.send(p, "SUBMIT_ANSWER", question_id=q["question_id"], attempt_id=new_id(),
                       choice_id=q["choices"][0]["choice_id"])
        self.assertEqual(code(stale), "STALE_ATTEMPT")
        rid = new_id()
        accepted = g.answer(p, 0, rid=rid)
        self.assertEqual(accepted["data"]["accepted"], True)
        self.assertNotIn("correct", accepted["data"])
        self.assertEqual(code(g.answer(p, 0, rid=rid)), "RESULT")          # same request again: same reply
        second = g.answer(p, 1)
        self.assertEqual(code(second), "ALREADY_ANSWERED")
        self.assertEqual(second["data"]["choice_id"], q["choices"][0]["choice_id"])
        self.assertEqual(code(g.send(g.host, "SUBMIT_ANSWER", question_id=q["question_id"],
                                     attempt_id=g.room.attempt_id, choice_id=q["choices"][0]["choice_id"])),
                         "NOT_PLAYER")
        self.assertEqual(code(g.send(p, "START_QUIZ", expected_control_rev=g.room.control_rev)), "NOT_HOST")

    def test_scores_use_server_time_and_close_when_everyone_answered(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.answer(g.players[0], 0)                           # correct at once: 1000
        g.clock.ms += 5_000
        g.answer(g.players[1], 0)                           # correct at half time: 750
        g.answer(g.players[2], 1)                           # wrong: 0
        g.answer(g.players[3], 0)
        self.assertEqual(g.room.phase, "OPEN")
        g.answer(g.players[4], 0)                           # last answer closes the question
        state = g.players[1].state()
        self.assertEqual(state["phase"], "RESULTS")
        self.assertEqual(state["last_result"]["reason"], "ALL_ANSWERED")
        self.assertEqual(state["last_result"]["own_result"], {
            "choice_id": g.questions[0]["choices"][0]["choice_id"], "correct": True,
            "points": 750, "total_score": 750})
        self.assertEqual([p.member.total_score for p in g.players], [1000, 750, 0, 750, 750])
        self.assertEqual(state["leaderboard"][0]["score"], 1000)
        self.assertEqual(len(state["leaderboard"][1]["players"]), 3)       # three tied on 750
        self.assertIsNone(g.host.state()["last_result"]["own_result"])

    def test_late_answer_is_rejected_even_if_the_timer_thread_has_not_run(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.answer(g.players[0], 0)
        g.clock.ms += 10_000                                # exactly the deadline; no tick() was called
        late = g.answer(g.players[1], 0)
        self.assertEqual(code(late), "ANSWER_LATE")
        self.assertEqual(g.players[1].state()["phase"], "RESULTS")
        self.assertEqual(g.players[1].state()["last_result"]["reason"], "TIMEOUT")
        self.assertEqual(g.players[1].member.total_score, 0)

    def test_timer_tick_closes_at_the_deadline(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.clock.ms += 9_999
        g.room.tick(1.0)
        self.assertEqual(g.room.phase, "OPEN")
        g.clock.ms += 1
        g.room.tick(1.0)
        self.assertEqual(g.host.state()["phase"], "RESULTS")

    def test_next_question_then_finish_releases_the_host(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        self.assertEqual(code(g.cmd("NEXT_QUESTION")), "INVALID_STATE")
        g.cmd("CLOSE_QUESTION")
        first_attempt = g.room.attempt_id
        g.cmd("NEXT_QUESTION")
        self.assertEqual(g.host.state()["question_index"], 1)
        self.assertNotEqual(g.room.attempt_id, first_attempt)
        g.cmd("CLOSE_QUESTION")
        g.cmd("NEXT_QUESTION")
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["finish_reason"]), ("FINISHED", "COMPLETED"))
        self.assertEqual(g.join("late").last("ERROR")["data"]["code"], "ROOM_FINISHED")
        new_room = FakeConn()
        g.registry.create(new_room, g.request("CREATE_ROOM", username="Teacher", host_token=g.host_token))
        self.assertEqual(new_room.last("RESULT")["data"]["role"], "HOST")


class HostControlTests(unittest.TestCase):
    def test_pause_freezes_the_clock_and_blocks_answers(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.clock.ms += 4_000
        g.cmd("PAUSE_QUIZ")
        self.assertEqual(g.players[0].state()["pause_reason"], "MANUAL")
        g.clock.ms += 60_000                                # a long pause costs nothing
        self.assertEqual(g.players[0].state()["remaining_ms"], 6_000)
        self.assertEqual(code(g.answer(g.players[0], 0)), "QUESTION_PAUSED")
        g.cmd("RESUME_QUIZ")
        self.assertEqual(g.room.phase, "OPEN")
        g.answer(g.players[0], 0)
        self.assertEqual(g.room.answers[g.players[0].member.actor_id]["remaining_ms"], 6_000)

    def test_stale_host_command_is_refused_and_replay_is_not_run_twice(self):
        g = Game(self)
        old = g.room.control_rev
        g.cmd("START_QUIZ")
        stale = g.cmd("CLOSE_QUESTION", rev=old)
        self.assertEqual(code(stale), "STALE_STATE")
        self.assertEqual(stale["data"]["current_control_rev"], g.room.control_rev)
        self.assertEqual(g.room.phase, "OPEN")
        rid, rev = new_id(), g.room.control_rev
        g.send(g.host, "PAUSE_QUIZ", rid=rid, expected_control_rev=rev)
        after = g.room.control_rev
        self.assertEqual(code(g.send(g.host, "PAUSE_QUIZ", rid=rid, expected_control_rev=rev)), "RESULT")
        self.assertEqual(g.room.control_rev, after)         # the repeat changed nothing
        self.assertEqual(code(g.send(g.host, "RESUME_QUIZ", rid=rid, expected_control_rev=after)),
                         "REQUEST_ID_REUSED")

    def test_remove_player(self):
        g = Game(self)
        victim = g.players[0]
        token, pid = victim.member.token, victim.member.actor_id
        g.cmd("START_QUIZ")
        g.answer(victim, 0)
        self.assertEqual(code(g.cmd("REMOVE_PLAYER", player_id=new_id())), "UNKNOWN_PLAYER")
        self.assertEqual(code(g.cmd("REMOVE_PLAYER", player_id=pid)), "RESULT")
        self.assertEqual(victim.last("REMOVED")["data"]["reason"], "HOST_REMOVED")
        self.assertTrue(victim.closed)
        back = g.resume(token)
        self.assertEqual(back.last("ERROR")["data"]["reason"], "HOST_REMOVED")
        rev = g.room.control_rev
        self.assertEqual(code(g.cmd("REMOVE_PLAYER", player_id=pid)), "RESULT")      # no-op
        self.assertEqual(g.room.control_rev, rev)
        g.cmd("CLOSE_QUESTION")
        self.assertEqual(g.room.players[pid].total_score, 1000)             # accepted answer still scored
        labels = [p["participation"] for row in g.host.state()["leaderboard"] for p in row["players"]]
        self.assertEqual(labels.count("REMOVED"), 1)

    def test_removing_the_last_unanswered_player_closes_the_question(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        for p in g.players[1:]:
            g.answer(p, 0)
        g.cmd("REMOVE_PLAYER", player_id=g.players[0].member.actor_id)
        self.assertEqual(g.host.state()["last_result"]["reason"], "ALL_ANSWERED")

    def test_close_room_discards_the_open_question_and_frees_the_host(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.answer(g.players[0], 0)
        g.cmd("CLOSE_ROOM")
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["finish_reason"]), ("FINISHED", "HOST_CLOSED"))
        self.assertIsNone(state["current_question"])
        self.assertEqual(g.players[0].member.total_score, 0)                # unclosed attempt not scored
        rev = g.room.control_rev
        self.assertEqual(code(g.cmd("CLOSE_ROOM")), "RESULT")
        self.assertEqual(g.room.control_rev, rev)
        self.assertEqual(g.room.tick(1.0), None)                            # timer thread stops
        new_room = FakeConn()
        g.registry.create(new_room, g.request("CREATE_ROOM", username="Teacher", host_token=g.host_token))
        self.assertEqual(new_room.last("RESULT")["data"]["role"], "HOST")

    def test_close_room_at_a_passed_deadline_scores_the_question_first(self):
        # The deadline passed but the timer thread has not run yet. The handler
        # applies the deadline before anything else: the question closes and is
        # scored, which changes control_rev, so the host's CLOSE_ROOM (decided
        # on the old picture) is refused as stale. A fresh one then works.
        g = Game(self)
        g.cmd("START_QUIZ")
        g.answer(g.players[0], 0)
        rev = g.room.control_rev
        g.clock.ms += 10_000
        self.assertEqual(code(g.cmd("CLOSE_ROOM", rev=rev)), "STALE_STATE")
        state = g.host.state()
        self.assertEqual((state["phase"], state["last_result"]["reason"]), ("RESULTS", "TIMEOUT"))
        self.assertEqual(g.players[0].member.total_score, 1000)            # scored before the room ended
        self.assertEqual(code(g.cmd("CLOSE_ROOM")), "RESULT")
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["finish_reason"]), ("FINISHED", "HOST_CLOSED"))
        self.assertEqual(state["self"]["total_score"], 1000)               # earned points are kept

    def test_close_room_racing_the_final_next_question(self):
        g = Game(self, questions=[question()])
        g.cmd("START_QUIZ")
        g.cmd("CLOSE_QUESTION")
        rev = g.room.control_rev                            # both commands decided on this picture
        self.assertEqual(code(g.cmd("NEXT_QUESTION", rev=rev)), "RESULT")
        self.assertEqual(code(g.cmd("CLOSE_ROOM", rev=rev)), "STALE_STATE")
        after = g.room.control_rev
        self.assertEqual(code(g.cmd("CLOSE_ROOM")), "RESULT")              # already finished: no-op
        self.assertEqual(g.room.control_rev, after)
        self.assertEqual(g.host.state()["finish_reason"], "COMPLETED")     # the first finish stays

    def test_lost_close_reply_then_resume_shows_the_finished_room(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.cmd("CLOSE_ROOM")
        g.room.disconnected(g.host)                         # the RESULT never reached the host
        g.host = g.resume(g.host_token)
        state = g.host.state()
        self.assertEqual((state["phase"], state["finish_reason"]), ("FINISHED", "HOST_CLOSED"))
        before = len(g.host.sent)
        self.assertEqual(code(g.cmd("CLOSE_ROOM")), "RESULT")              # asking again changes nothing
        self.assertEqual([m["type"] for m in g.host.sent[before:]], ["RESULT"])


class EditingTests(unittest.TestCase):
    def test_editing_the_current_question_restarts_it_as_a_new_attempt(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.clock.ms += 4_000
        g.answer(g.players[0], 0)
        old_attempt = g.room.attempt_id
        self.assertEqual(code(g.cmd("EDIT_BEGIN", question_id=new_id())), "STALE_ATTEMPT")
        g.cmd("EDIT_BEGIN", question_id=g.questions[0]["question_id"])
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["pause_reason"], state["editing"]), ("PAUSED", "EDITING", True))
        self.assertEqual(code(g.cmd("RESUME_QUIZ")), "INVALID_STATE")       # not while editing
        self.assertEqual(code(g.cmd("CLOSE_QUESTION")), "INVALID_STATE")
        replacement = {**question(time_limit_ms=30_000), "question_id": g.questions[0]["question_id"]}
        self.assertEqual(code(g.cmd("EDIT_COMMIT", question=question())), "STALE_ATTEMPT")
        g.cmd("EDIT_COMMIT", question=replacement)
        state = g.players[0].state()
        self.assertEqual((state["pause_reason"], state["editing"]), ("MANUAL", False))
        self.assertNotEqual(state["current_question"]["attempt_id"], old_attempt)
        self.assertEqual(state["remaining_ms"], 30_000)                     # full time of the new version
        self.assertIsNone(state["self"]["submitted_choice_id"])             # the old answer is gone
        self.assertEqual(g.players[0].member.total_score, 0)                # and was not scored
        g.cmd("RESUME_QUIZ")
        old = g.send(g.players[0], "SUBMIT_ANSWER", question_id=replacement["question_id"],
                     attempt_id=old_attempt, choice_id=g.questions[0]["choices"][0]["choice_id"])
        self.assertEqual(code(old), "STALE_ATTEMPT")                        # an answer to the old version
        self.assertEqual(code(g.answer(g.players[0], 0)), "RESULT")

    def test_cancelling_an_edit_keeps_question_answers_and_time(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.clock.ms += 4_000
        g.answer(g.players[0], 1)
        attempt = g.room.attempt_id
        g.cmd("EDIT_BEGIN", question_id=g.questions[0]["question_id"])
        self.assertEqual(code(g.cmd("EDIT_BEGIN", question_id=g.questions[0]["question_id"])), "INVALID_STATE")
        g.cmd("EDIT_CANCEL")
        self.assertEqual(code(g.cmd("EDIT_CANCEL")), "INVALID_STATE")
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["pause_reason"], state["remaining_ms"]),
                         ("PAUSED", "MANUAL", 6_000))
        self.assertEqual(state["current_question"]["attempt_id"], attempt)
        self.assertIsNotNone(state["self"]["submitted_choice_id"])

    def test_closing_the_room_while_editing_discards_the_edit_and_the_attempt(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.answer(g.players[0], 0)
        g.cmd("EDIT_BEGIN", question_id=g.questions[0]["question_id"])
        self.assertEqual(code(g.cmd("CLOSE_ROOM")), "RESULT")
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["finish_reason"], state["editing"], state["pause_reason"]),
                         ("FINISHED", "HOST_CLOSED", False, None))
        self.assertIsNone(state["current_question"])
        self.assertEqual(state["self"]["total_score"], 0)                  # the unclosed attempt is not scored
        replacement = {**question(), "question_id": g.questions[0]["question_id"]}
        self.assertEqual(code(g.cmd("EDIT_COMMIT", question=replacement)), "INVALID_STATE")

    def test_host_reconnecting_during_an_edit_can_still_commit(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.cmd("EDIT_BEGIN", question_id=g.questions[0]["question_id"])
        g.room.disconnected(g.host)
        self.assertEqual(g.players[0].state()["pause_reason"], "EDITING")  # still editing, not a new pause
        g.host = g.resume(g.host_token)
        self.assertTrue(g.host.state()["editing"])
        replacement = {**question(text="Fixed"), "question_id": g.questions[0]["question_id"]}
        self.assertEqual(code(g.cmd("EDIT_COMMIT", question=replacement)), "RESULT")
        self.assertEqual(g.host.state()["current_question"]["text"], "Fixed")

    def test_future_questions_can_change_but_played_ones_are_locked(self):
        third = question()
        g = Game(self, questions=[question(), question(), third])
        first, second = g.questions[0], g.questions[1]
        g.cmd("START_QUIZ")
        self.assertEqual(code(g.cmd("EDIT_QUESTION", question=first)), "INVALID_STATE")       # current: EDIT_BEGIN
        g.cmd("CLOSE_QUESTION")
        g.cmd("NEXT_QUESTION")                                              # now on question 2
        self.assertEqual(code(g.cmd("EDIT_QUESTION", question=first)), "QUESTION_LOCKED")
        self.assertEqual(code(g.cmd("DELETE_QUESTION", question_id=first["question_id"])), "QUESTION_LOCKED")
        self.assertEqual(code(g.cmd("DELETE_QUESTION", question_id=new_id())), "INVALID_QUIZ")
        extra = question(text="Added during play")
        self.assertEqual(code(g.cmd("ADD_QUESTION", question=extra, before_question_id=third["question_id"])),
                         "RESULT")
        self.assertEqual(code(g.cmd("ADD_QUESTION", question=extra, before_question_id=None)), "INVALID_QUIZ")
        self.assertEqual(code(g.cmd("ADD_QUESTION", question=question(),
                                    before_question_id=second["question_id"])), "INVALID_STATE")
        ids = [q["question_id"] for q in g.room.quiz["questions"]]
        self.assertEqual(ids, [first["question_id"], second["question_id"], extra["question_id"],
                               third["question_id"]])
        self.assertEqual(g.players[0].state()["question_count"], 4)
        self.assertEqual(code(g.cmd("REORDER_QUESTIONS", question_ids=[third["question_id"]])), "INVALID_ORDER")
        g.cmd("REORDER_QUESTIONS", question_ids=[third["question_id"], extra["question_id"]])
        g.cmd("DELETE_QUESTION", question_id=extra["question_id"])
        g.cmd("CLOSE_QUESTION")
        g.cmd("NEXT_QUESTION")
        self.assertEqual(g.room.current["question_id"], third["question_id"])
        g.cmd("CLOSE_QUESTION")
        g.cmd("NEXT_QUESTION")                                              # the deleted one is never played
        self.assertEqual(g.host.state()["phase"], "FINISHED")

    def test_question_limits_and_frozen_history(self):
        g = Game(self, questions=[question()])
        self.assertEqual(code(g.cmd("DELETE_QUESTION", question_id=g.questions[0]["question_id"])),
                         "QUESTION_LIMIT")
        for _ in range(29):
            g.cmd("ADD_QUESTION", question=question(), before_question_id=None)
        self.assertEqual(code(g.cmd("ADD_QUESTION", question=question(), before_question_id=None)),
                         "QUESTION_LIMIT")
        self.assertEqual(code(g.cmd("EDIT_QUESTION", question={**question(choices=9),
                                    "question_id": g.questions[0]["question_id"]})), "INVALID_QUIZ")
        g.cmd("START_QUIZ")
        g.cmd("CLOSE_ROOM")
        g.cmd("DELETE_QUESTION", question_id=g.questions[0]["question_id"])   # reusable quiz stays editable
        self.assertEqual(g.host.state()["question_count"], 30)              # the finished session's count is frozen
        self.assertEqual(len(g.host.state()["quiz"]["questions"]), 29)

    def test_room_refuses_growth_that_could_not_fit_in_one_frame(self):
        g = Game(self, players=1)
        with mock.patch("fatrat.server.room.MAX_FRAME", g.room.quiz_bytes + 24_000 + 400):
            self.assertEqual(g.join("second").last("ERROR")["data"]["code"], "SERVER_BUSY")
            self.assertEqual(code(g.cmd("ADD_QUESTION", question=question(), before_question_id=None)),
                             "INVALID_QUIZ")
        self.assertIsNotNone(g.join("second").member)


class DisconnectTests(unittest.TestCase):
    def test_disconnected_player_stays_eligible_and_can_resume(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        gone = g.players[0]
        token, actor = gone.member.token, gone.member.actor_id
        g.answer(gone, 1)
        g.room.disconnected(gone)
        self.assertFalse(g.host.state()["participants"][0]["connected"])
        for p in g.players[1:4]:
            g.answer(p, 0)
        self.assertEqual(g.room.phase, "OPEN")
        back = g.resume(token)
        identity = back.last("RESULT")["data"]
        self.assertEqual(identity["actor_id"], actor)
        self.assertEqual(back.state()["self"]["submitted_choice_id"],
                         g.questions[0]["choices"][1]["choice_id"])
        self.assertEqual(back.state()["live"], {"answered_count": 4, "eligible_count": 5})
        self.assertEqual(g.resume(new_token()).last("ERROR")["data"]["code"], "INVALID_TOKEN")

    def test_host_disconnect_pauses_until_the_host_resumes_explicitly(self):
        g = Game(self)
        g.cmd("START_QUIZ")
        g.clock.ms += 3_000
        g.room.disconnected(g.host)
        state = g.players[0].state()
        self.assertEqual((state["phase"], state["pause_reason"], state["host_connected"]),
                         ("PAUSED", "HOST_DISCONNECTED", False))
        g.clock.ms += 30_000
        g.host = g.resume(g.host_token)
        self.assertEqual(g.host.state()["phase"], "PAUSED")                 # reconnecting alone does not resume
        self.assertTrue(g.players[0].state()["host_connected"])
        g.cmd("RESUME_QUIZ")
        self.assertEqual(g.players[0].state()["remaining_ms"], 7_000)

    def test_second_connection_replaces_the_first(self):
        g = Game(self)
        old = g.players[0]
        new = g.resume(old.member.token)
        self.assertEqual(old.last("SESSION_REPLACED")["data"]["reason"], "RECONNECTED_ELSEWHERE")
        self.assertTrue(old.closed)
        self.assertIsNone(old.member)
        g.room.disconnected(old)                            # the old socket closing must not detach the new one
        self.assertIs(new.member.conn, new)

    def test_leave_revokes_the_token_and_keeps_the_score_row(self):
        g = Game(self)
        token = g.players[0].member.token
        g.send(g.players[0], "LEAVE_ROOM")
        self.assertEqual(g.resume(token).last("ERROR")["data"]["reason"], "LEFT_ROOM")
        labels = [p["participation"] for row in g.host.state()["leaderboard"] for p in row["players"]]
        self.assertEqual(sorted(labels), ["ACTIVE"] * 4 + ["LEFT"])


class UdpTests(unittest.TestCase):
    def bind(self, g: Game, conn: FakeConn):
        g.room.bind_udp(conn, new_id(), 43001)
        b = conn.last("RESULT")["data"]
        hello = {"v": 1, "type": "UDP_HELLO", "session_id": conn.member.session_id,
                 "binding_id": b["binding_id"], "data": {"nonce": b["udp_nonce"]}}
        return b, hello

    def test_hello_must_carry_the_nonce_and_come_from_the_tcp_peer_address(self):
        g = Game(self)
        p = g.players[0]
        b, hello = self.bind(g, p)
        wrong = {**hello, "data": {"nonce": "0" * 32}}
        self.assertEqual(g.room.confirm_udp(p.member, wrong, ("10.0.0.2", 7000)), "wrong nonce")
        self.assertIn("source IP", g.room.confirm_udp(p.member, hello, ("10.9.9.9", 7000)))
        self.assertIsNone(g.room.confirm_udp(p.member, hello, ("10.0.0.2", 7000)))
        self.assertEqual(p.last("UDP_BOUND")["data"]["binding_id"], b["binding_id"])

    def test_live_status_only_for_confirmed_bindings_with_increasing_seq(self):
        g = Game(self)
        p, other = g.players[0], g.players[1]
        _, hello = self.bind(g, p)
        g.room.confirm_udp(p.member, hello, ("10.0.0.2", 7000))
        self.bind(g, other)                                 # requested but never confirmed
        self.assertEqual(g.room.tick(1.0)[0], [])           # lobby: nothing live to report
        g.cmd("START_QUIZ")
        g.answer(other, 0)
        seqs = []
        for _ in range(3):
            g.clock.ms += 1_000
            datagrams, _ = g.room.tick(1.0)
            self.assertEqual(len(datagrams), 1)
            payload, addr = datagrams[0]
            self.assertLessEqual(len(payload), udp.MAX_DATAGRAM)
            message = decode_json(payload)
            seqs.append(message["seq"])
            self.assertEqual(addr, ("10.0.0.2", 7000))
        self.assertEqual(seqs, [1, 2, 3])
        self.assertEqual(message["data"], {"remaining_ms": 7_000, "answered_count": 1, "eligible_count": 5})

    def test_reconnect_drops_the_old_binding_and_a_new_one_starts_again(self):
        g = Game(self)
        p = g.players[0]
        old, old_hello = self.bind(g, p)
        g.room.confirm_udp(p.member, old_hello, ("10.0.0.2", 7000))
        g.cmd("START_QUIZ")
        g.room.tick(1.0)
        g.room.tick(1.0)                                    # seq 1 and 2 went to the old address
        back = g.resume(p.member.token)                     # reconnect on a new TCP connection
        self.assertNotIn(old["binding_id"], g.udp.bindings)   # the old binding is gone with the old session
        self.assertEqual(g.room.tick(1.0)[0], [])           # nothing is sent until the new client binds
        self.assertEqual(g.room.confirm_udp(back.member, old_hello, ("10.0.0.2", 7000)), "old binding")
        new, hello = self.bind(g, back)
        self.assertNotEqual(new["binding_id"], old["binding_id"])
        self.assertIsNone(g.room.confirm_udp(back.member, hello, ("10.0.0.2", 7001)))
        self.assertIn("fresh REQUEST_UDP_BIND", g.room.confirm_udp(back.member, hello, ("10.0.0.2", 7002)))
        datagrams, _ = g.room.tick(1.0)
        payload, addr = datagrams[0]
        self.assertEqual((addr, decode_json(payload)["seq"]), (("10.0.0.2", 7001), 1))   # new binding, seq from 1


if __name__ == "__main__":
    unittest.main()
