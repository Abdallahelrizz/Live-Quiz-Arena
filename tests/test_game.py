"""End-to-end tests: the real server and real clients over real TCP and UDP sockets.

The server runs in this process on 127.0.0.1 with free ports. The clients
are the same ClientSession the terminal clients and the interface use.
These tests take a few seconds because real question timers run.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from fatrat.client.bot import HostBot, PlayerBot, run_rooms
from fatrat.client.core import ClientSession, NotConnected, RequestFailed
from fatrat.client.storage import Profile
from fatrat.client.terminal_host import attach, run_command
from fatrat.common import new_token
from fatrat.server.main import GameServer
from tests.samples import question


def wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


_patchers = [mock.patch(target, lambda *parts: None) for target in (
    "fatrat.server.main.log", "fatrat.server.room.log", "fatrat.server.registry.log",
    "fatrat.server.udp_service.log", "fatrat.server.connection.log")]


def setUpModule():
    for patcher in _patchers:          # keep the test output free of server log lines
        patcher.start()


def tearDownModule():
    time.sleep(0.2)                    # let server threads notice the closed sockets first
    for patcher in _patchers:
        patcher.stop()


class GameTestCase(unittest.TestCase):
    def setUp(self):
        self.server = GameServer("127.0.0.1", tcp_port=0, udp_port=0, interval=0.2)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.close)
        self.port = self.server.tcp_port
        self.clients = []
        self.addCleanup(lambda: [c.close() for c in self.clients])

    def client(self, **kwargs) -> ClientSession:
        session = ClientSession("127.0.0.1", self.port, **kwargs)
        session.connect()
        self.clients.append(session)
        return session

    def room(self, players: int = 5, seconds: float = 3.0, questions: int = 1):
        """A host with a loaded quiz and `players` joined players (plain sessions, no automatic answers)."""
        host = self.client()
        self.host_token = new_token()
        host.create_room("Teacher", self.host_token)
        quiz = [question(time_limit_ms=int(seconds * 1000)) for _ in range(questions)]
        host.host_command("PUT_QUIZ", title="Test", questions=quiz)
        joined = []
        for i in range(players):
            p = self.client()
            p.join(host.identity["pin"], f"p{i + 1}")
            joined.append(p)
        self.assertTrue(wait_until(lambda: len(host.state["participants"]) == players))
        return host, joined, quiz


class FullGameTests(GameTestCase):
    def test_three_rooms_of_five_run_at_the_same_time(self):
        questions = [question(time_limit_ms=2_000), question(choices=8, time_limit_ms=2_000)]
        summaries = run_rooms("127.0.0.1", self.port, rooms=3, players=5, title="Rooms",
                              questions=questions, results_pause=0.1, quiet=True)
        self.assertEqual(len({s["pin"] for s in summaries}), 3)
        for k, summary in enumerate(summaries):
            names = sorted(p["username"] for row in summary["leaderboard"] for p in row["players"])
            self.assertEqual(names, [f"r{k + 1}-p{i + 1}" for i in range(5)])     # only its own players
            self.assertEqual(summary["answers_accepted"], 10)                    # 5 players x 2 questions
            self.assertGreater(summary["live_snapshots"], 0)                     # UDP reached the players
            board = {p["username"]: row["score"] for row in summary["leaderboard"] for p in row["players"]}
            self.assertEqual(board, summary["scores"])                           # each player's own total agrees

    def test_join_errors_and_duplicate_answer_over_the_network(self):
        host, players, quiz = self.room()
        pin = host.identity["pin"]
        for bad_pin, name, expected in (("000000" if pin != "000000" else "111111", "x", "INVALID_PIN"),
                                        (pin, "P1", "NAME_TAKEN"), (pin, "", "INVALID_USERNAME")):
            with self.assertRaises(RequestFailed) as caught:
                self.client().join(bad_pin, name)
            self.assertEqual(caught.exception.code, expected)
        host.host_command("START_QUIZ")
        p = players[0]
        self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))
        choice = p.state["current_question"]["choices"][0]["choice_id"]
        self.assertTrue(p.submit_answer(choice)["accepted"])
        with self.assertRaises(RequestFailed) as caught:
            p.submit_answer(choice)
        self.assertEqual(caught.exception.code, "ALREADY_ANSWERED")
        with self.assertRaises(RequestFailed) as caught:
            p.host_command("CLOSE_QUESTION")
        self.assertEqual(caught.exception.code, "NOT_HOST")

    def test_question_times_out_and_late_answer_is_refused(self):
        host, players, quiz = self.room(seconds=1.0)
        host.host_command("START_QUIZ")
        p = players[0]
        self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))
        q = p.state["current_question"]
        self.assertTrue(wait_until(lambda: p.state["phase"] == "RESULTS", timeout=3))
        self.assertEqual(p.state["last_result"]["reason"], "TIMEOUT")
        with self.assertRaises(RequestFailed) as caught:
            p.request("SUBMIT_ANSWER", {"question_id": q["question_id"], "attempt_id": q["attempt_id"],
                                        "choice_id": q["choices"][0]["choice_id"]})
        self.assertEqual(caught.exception.code, "ANSWER_LATE")


class RobustnessTests(GameTestCase):
    def test_player_connection_drop_then_automatic_resume_keeps_the_answer(self):
        host, players, quiz = self.room(seconds=20)
        host.host_command("START_QUIZ")
        p = players[0]
        self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))
        choice = p.state["current_question"]["choices"][1]["choice_id"]
        p.submit_answer(choice)
        actor = p.identity["actor_id"]
        with mock.patch("fatrat.client.core.RECONNECT_DELAYS", (0.1,)):
            p._sock.shutdown(2)                                    # the network drops; no LEAVE_ROOM is sent
            self.assertTrue(wait_until(lambda: not host.state["participants"][0]["connected"]))
            self.assertEqual(host.state["phase"], "OPEN")          # one player dropping does not stop the room
            self.assertTrue(wait_until(lambda: host.state["participants"][0]["connected"], timeout=5))
        self.assertTrue(wait_until(lambda: p.state["self"]["submitted_choice_id"] == choice))
        self.assertEqual(p.identity["actor_id"], actor)            # same player, new session

    def test_host_drop_pauses_the_question_and_host_returns_to_the_same_room(self):
        host, players, quiz = self.room(seconds=20)
        host.host_command("START_QUIZ")
        host.auto_reconnect = False
        host._sock.shutdown(2)
        p = players[0]
        self.assertTrue(wait_until(lambda: p.state["phase"] == "PAUSED"))
        self.assertEqual(p.state["pause_reason"], "HOST_DISCONNECTED")
        again = self.client()
        with self.assertRaises(RequestFailed) as caught:           # the same host cannot open a second room
            again.create_room("Teacher", self.host_token)
        self.assertEqual(caught.exception.code, "HOST_HAS_ROOM")
        again.resume(caught.exception.data["existing_room_id"], self.host_token)
        self.assertEqual(again.state["phase"], "PAUSED")
        again.host_command("RESUME_QUIZ")
        self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))

    def test_bad_bytes_close_only_the_offending_connection(self):
        host, players, quiz = self.room()
        vandal = self.client(auto_reconnect=False)
        vandal._sock.sendall(b"\xff\xff\xff\xff garbage")          # frame length far above 1 MiB
        self.assertTrue(wait_until(lambda: vandal._sock is None))
        second = self.client(auto_reconnect=False)
        second._sock.sendall(b"\x00\x00\x00\x05{bad}")             # right length, not JSON
        self.assertTrue(wait_until(lambda: second._sock is None))
        self.assertEqual(host.request("GET_STATE", {}), {})        # the room and its host are untouched
        self.assertEqual(players[0].request("GET_STATE", {}), {})

    def test_silent_client_is_dropped_by_the_heartbeat_but_a_live_one_is_not(self):
        self.server.ping_interval, self.server.pong_timeout = 0.2, 0.6
        host, players, quiz = self.room()
        silent = players[0]
        silent.auto_reconnect = False
        silent._on_message = lambda m: None                        # this client stops answering PING
        self.assertTrue(wait_until(lambda: not host.state["participants"][0]["connected"], timeout=4))
        time.sleep(1.0)                                            # several PING rounds for the others
        self.assertTrue(all(p["connected"] for p in host.state["participants"][1:]))
        self.assertEqual(host.request("GET_STATE", {}), {})

    def test_flooding_client_is_rate_limited_and_others_are_not_slowed(self):
        host, players, quiz = self.room()
        flooder = players[0]
        codes = []
        for _ in range(40):
            try:
                flooder.request("GET_STATE", {}, _retry=False)
                codes.append("RESULT")
            except RequestFailed as exc:
                codes.append(exc.code)
                self.assertTrue(exc.data["retryable"])
                self.assertGreater(exc.data["retry_after_ms"], 0)
        self.assertIn("RATE_LIMITED", codes)
        self.assertGreaterEqual(codes.count("RESULT"), 15)         # the allowed burst went through
        self.assertEqual(players[1].request("GET_STATE", {}), {})  # another client is unaffected
        self.assertEqual(flooder.request("GET_STATE", {}), {})     # the normal call waits and retries

    def test_lost_duplicated_and_reordered_udp_never_moves_the_countdown_back(self):
        self.server.udp.impair = True                              # the server misbehaves on purpose
        host, players, quiz = self.room(seconds=4)
        p = players[0]
        seen = []
        p.on_event = lambda kind, payload: seen.append(payload["remaining_ms"]) if kind == "live" else None
        self.assertTrue(p.enable_udp())
        host.host_command("START_QUIZ")
        self.assertTrue(wait_until(lambda: p.state["phase"] == "RESULTS", timeout=7))
        self.assertGreater(len(seen), 5)
        self.assertEqual(seen, sorted(seen, reverse=True))         # accepted snapshots only ever count down
        from fatrat.protocol import udp
        self.assertGreater(p.udp_dropped.get(udp.DUPLICATE, 0), 0)
        self.assertGreater(p.udp_dropped.get(udp.OUT_OF_ORDER, 0), 0)
        self.assertEqual(p.state["last_result"]["reason"], "TIMEOUT")   # the result came by TCP regardless

    def test_host_adds_a_question_during_play_over_the_network(self):
        host, players, quiz = self.room(seconds=20)
        host.host_command("START_QUIZ")
        extra = question(text="Added live")
        host.host_command("ADD_QUESTION", question=extra, before_question_id=None)
        self.assertTrue(wait_until(lambda: players[0].state["question_count"] == 2))
        with self.assertRaises(RequestFailed) as caught:
            host.host_command("DELETE_QUESTION", question_id=quiz[0]["question_id"])
        self.assertEqual(caught.exception.code, "INVALID_STATE")
        host.host_command("CLOSE_QUESTION")
        host.host_command("NEXT_QUESTION")
        self.assertTrue(wait_until(lambda: host.state["phase"] == "OPEN"))
        self.assertEqual(host.state["current_question"]["text"], "Added live")

    def test_udp_silence_triggers_a_tcp_refresh_and_the_game_goes_on(self):
        host, players, quiz = self.room(seconds=2)
        p = players[0]
        self.assertTrue(p.enable_udp())                            # bound, then UDP goes completely silent
        self.server.udp.send = lambda payload, addr: None          # e.g. a firewall drops every datagram
        asked = []
        ask = p.request
        p.request = lambda mtype, data, *args, **kwargs: (asked.append(mtype), ask(mtype, data, *args, **kwargs))[1]
        with mock.patch("fatrat.client.core.UDP_QUIET_SECONDS", 0.3), \
                mock.patch("fatrat.client.core.UDP_REFRESH_SECONDS", 0.3):
            host.host_command("START_QUIZ")
            self.assertTrue(wait_until(lambda: "GET_STATE" in asked, timeout=2))
            self.assertTrue(p.countdown_is_stale())                # the screen can mark the time as approximate
            self.assertTrue(wait_until(lambda: p.state["phase"] == "RESULTS", timeout=4))
        self.assertEqual(p.state["last_result"]["reason"], "TIMEOUT")   # the question closed by TCP anyway

    def test_lost_create_reply_is_recovered_without_a_second_room(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        profile = Profile(os.path.join(folder.name, "profile.json"))
        server = f"127.0.0.1:{self.port}"
        saved_rid = profile.pending_create(server, "Teacher")       # what attach() will send
        first = self.client(auto_reconnect=False)
        deliver = first._on_message

        def lose_the_reply(message):
            if message.get("reply_to") == saved_rid:
                first._sock.shutdown(socket.SHUT_RDWR)            # the network fails as the reply arrives
                return
            deliver(message)

        first._on_message = lose_the_reply
        with mock.patch("fatrat.client.terminal_host.say"):
            with self.assertRaises(NotConnected):
                attach(first, profile, server, "Teacher")
            self.assertEqual(self.server.registry.room_count(), 1)  # the server did create the room
            second = self.client()                                  # the host runs the program again
            attach(second, profile, server, "Teacher")
            self.assertEqual(self.server.registry.room_count(), 1)  # same request: no second room
            room_id = second.identity["room_id"]
            self.assertIn(room_id, self.server.registry.by_id)
            self.assertEqual(second.state["self"]["role"], "HOST")
            self.assertIsNone(profile._data["pending"].get(server))   # the request is settled

            # If the saved request belongs to a room that has since finished,
            # the host gets a genuinely new room on a fresh connection.
            second.host_command("CLOSE_ROOM")
            profile._data["pending"][server] = {"request_id": saved_rid, "username": "Teacher"}
            third = self.client()
            attach(third, profile, server, "Teacher")
        self.assertEqual(self.server.registry.room_count(), 2)
        self.assertNotEqual(third.identity["room_id"], room_id)
        self.assertEqual(third.state["phase"], "LOBBY")

    def test_terminal_host_lists_edits_and_moves_questions(self):
        host, players, quiz = self.room(seconds=20, questions=3)
        ids = [q["question_id"] for q in quiz]
        typed = iter(())
        with mock.patch("fatrat.client.terminal_host.say") as said, \
                mock.patch("builtins.input", side_effect=lambda prompt="": next(typed)):
            shown = lambda: " ".join(str(c.args[0]) for c in said.call_args_list)

            run_command(host, "move 3 2")                           # lobby: every question can move
            self.assertEqual([q["question_id"] for q in host.state["quiz"]["questions"]],
                             [ids[0], ids[2], ids[1]])
            host.host_command("START_QUIZ")
            p = players[0]
            self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))
            p.submit_answer(p.state["current_question"]["choices"][0]["choice_id"])
            old_attempt = p.state["current_question"]["attempt_id"]

            typed = iter(["Edited live?", "", "", "30", "y"])       # new text, keep choices and answer, 30 s, save
            run_command(host, "edit")
            self.assertEqual(host.state["current_question"]["text"], "Edited live?")
            self.assertEqual((host.state["phase"], host.state["remaining_ms"]), ("PAUSED", 30_000))
            self.assertTrue(wait_until(lambda: p.state["current_question"]["attempt_id"] != old_attempt))
            self.assertIsNone(p.state["self"]["submitted_choice_id"])   # the old answer was dropped

            typed = iter(["Never saved", "", "", "", "n"])          # change of mind: EDIT_CANCEL
            run_command(host, "edit")
            self.assertEqual((host.state["editing"], host.state["pause_reason"]), (False, "MANUAL"))
            self.assertEqual(host.state["current_question"]["text"], "Edited live?")

            typed = iter(["", "a | b | c", "3", "", "y"])           # a future question: three choices, third correct
            run_command(host, "edit 3")
            third = host.state["quiz"]["questions"][2]
            self.assertEqual([c["text"] for c in third["choices"]], ["a", "b", "c"])
            self.assertEqual(third["correct_choice_id"], third["choices"][2]["choice_id"])

            run_command(host, "questions")
            self.assertIn("[on screen now]", shown())
            run_command(host, "move 1 2")                           # question 1 is on screen: refused here
            self.assertIn("Only questions 2 to 3 can still be moved.", shown())
            run_command(host, "resume")
            self.assertTrue(wait_until(lambda: p.state["phase"] == "OPEN"))

    def test_udp_snapshots_arrive_and_only_after_binding(self):
        host, players, quiz = self.room(seconds=3)
        p = players[0]
        seen = []
        p.on_event = lambda kind, payload: seen.append(payload) if kind == "live" else None
        self.assertTrue(p.enable_udp())
        host.host_command("START_QUIZ")
        self.assertTrue(wait_until(lambda: len(seen) >= 3, timeout=3))
        self.assertEqual(seen[0]["eligible_count"], 5)
        self.assertGreaterEqual(seen[0]["remaining_ms"], seen[-1]["remaining_ms"])   # never goes backwards
        self.assertLessEqual(abs(p.countdown_ms() - seen[-1]["remaining_ms"]), 400)


if __name__ == "__main__":
    unittest.main()
