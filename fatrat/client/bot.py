"""Simulated hosts and players.

The project allows "testing scripts to simulate multiple players and rooms".
These bots are real clients: each has its own TCP connection and UDP socket
and uses the same ClientSession as a human player.

Fill a room that a person is hosting (PIN from the host's screen):

    python3 -m fatrat.client.bot --server 192.168.1.35 --pin 123456 --players 5

Run whole rooms by themselves, hosts included (the three-rooms demonstration):

    python3 -m fatrat.client.bot --server 192.168.1.35 --rooms 3 --players 5 --quiz quizzes/quick-demo.json

Bot players pick a random choice after a random delay; they cannot know the
right answer, because the server never sends it to players before the
question closes.
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from typing import List, Optional

from fatrat.client.core import ClientSession, NotConnected, RequestFailed
from fatrat.client.quizfile import load_quiz
from fatrat.common import TCP_PORT, new_token, say


class PlayerBot:
    """One simulated player."""

    def __init__(self, server: str, port: int, name: str, max_delay: float = 4.0, use_udp: bool = True):
        self.name, self.max_delay, self.use_udp = name, max_delay, use_udp
        self.session = ClientSession(server, port, on_event=self._on_event)
        self.finished = threading.Event()
        self.live_snapshots = 0
        self.answers_accepted = 0
        self._answered_attempt = None
        self._lock = threading.Lock()

    def join(self, pin: str) -> None:
        self.session.connect()
        self.session.join(pin, self.name)
        if self.use_udp:
            threading.Thread(target=self.session.enable_udp, daemon=True).start()

    def _on_event(self, kind: str, payload) -> None:
        if kind == "live":
            self.live_snapshots += 1
        elif kind == "notice":
            self.finished.set()
        elif kind == "state":
            if payload["phase"] == "FINISHED":
                self.finished.set()
            q = payload["current_question"]
            if payload["phase"] == "OPEN" and payload["self"]["submitted_choice_id"] is None:
                with self._lock:
                    if self._answered_attempt == q["attempt_id"]:
                        return
                    self._answered_attempt = q["attempt_id"]
                delay = random.uniform(0.2, min(self.max_delay, q["time_limit_ms"] / 1000 * 0.6))
                threading.Timer(delay, self._answer, args=(q,)).start()

    def _answer(self, q: dict) -> None:
        try:
            self.session.request("SUBMIT_ANSWER", {
                "question_id": q["question_id"], "attempt_id": q["attempt_id"],
                "choice_id": random.choice(q["choices"])["choice_id"]})
            self.answers_accepted += 1
        except (RequestFailed, NotConnected):
            pass                                   # closed, paused or disconnected meanwhile: nothing to do

    def final_score(self) -> Optional[int]:
        return self.session.state["self"]["total_score"] if self.session.state else None

    def close(self) -> None:
        self.session.auto_reconnect = False
        self.session.close()


class HostBot:
    """One simulated host: creates a room, loads a quiz, runs it to the end."""

    def __init__(self, server: str, port: int, name: str, title: str, questions: list,
                 results_pause: float = 2.0):
        self.name, self.title, self.questions, self.results_pause = name, title, questions, results_pause
        self.session = ClientSession(server, port)
        self.pin: Optional[str] = None

    def open_room(self) -> str:
        self.session.connect()
        self.session.create_room(self.name, new_token())   # a fresh identity: every bot host is a different host
        self.session.host_command("PUT_QUIZ", title=self.title, questions=self.questions)
        threading.Thread(target=self.session.enable_udp, daemon=True).start()
        self.pin = self.session.identity["pin"]
        return self.pin

    def _wait(self, condition, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition(self.session.state):
                return True
            time.sleep(0.05)
        return False

    def run(self, expected_players: int) -> list:
        """Wait for the players, play every question and return the final leaderboard."""
        connected = lambda s: sum(1 for p in s["participants"] if p["connected"]) >= expected_players
        if not self._wait(connected, 30):
            raise RuntimeError(f"room {self.pin}: players did not arrive")
        self.session.host_command("START_QUIZ")
        while True:
            limit = max(q["time_limit_ms"] for q in self.questions) / 1000
            if not self._wait(lambda s: s["phase"] in ("RESULTS", "FINISHED"), limit + 10):
                raise RuntimeError(f"room {self.pin}: question did not close")
            if self.session.state["phase"] == "FINISHED":
                return self.session.state["leaderboard"]
            time.sleep(self.results_pause)                 # time for people to read the results
            try:
                self.session.host_command("NEXT_QUESTION")
            except RequestFailed as exc:
                if exc.code != "STALE_STATE":
                    raise
            self._wait(lambda s: s["phase"] != "RESULTS", 5)

    def close(self) -> None:
        self.session.auto_reconnect = False
        self.session.close()


def run_rooms(server: str, port: int, rooms: int, players: int, title: str, questions: list,
              results_pause: float = 2.0, quiet: bool = False) -> List[dict]:
    """Run `rooms` complete rooms at the same time. Returns one summary per room."""
    summaries: List[Optional[dict]] = [None] * rooms

    def one_room(k: int) -> None:
        host = HostBot(server, port, f"host-{k + 1}", f"{title} (room {k + 1})", questions, results_pause)
        bots = [PlayerBot(server, port, f"r{k + 1}-p{i + 1}") for i in range(players)]
        try:
            pin = host.open_room()
            if not quiet:
                say(f"  room {k + 1}: PIN {pin}")
            joiners = [threading.Thread(target=b.join, args=(pin,)) for b in bots]
            for t in joiners:
                t.start()
            for t in joiners:
                t.join()
            board = host.run(players)
            for b in bots:
                b.finished.wait(5)
            summaries[k] = {"pin": pin, "leaderboard": board,
                            "scores": {b.name: b.final_score() for b in bots},
                            "live_snapshots": sum(b.live_snapshots for b in bots),
                            "answers_accepted": sum(b.answers_accepted for b in bots)}
        finally:
            host.close()
            for b in bots:
                b.close()

    threads = [threading.Thread(target=one_room, args=(k,)) for k in range(rooms)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat simulated players and hosts.")
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("--players", type=int, default=5, help="players per room")
    parser.add_argument("--pin", help="join this existing room instead of creating rooms")
    parser.add_argument("--rooms", type=int, default=1, help="rooms to create and run (without --pin)")
    parser.add_argument("--quiz", default="quizzes/quick-demo.json")
    parser.add_argument("--prefix", default="bot", help="name prefix of the players joining --pin")
    args = parser.parse_args()
    if args.pin:
        bots = [PlayerBot(args.server, args.port, f"{args.prefix}-{i + 1}") for i in range(args.players)]
        for bot in bots:
            try:
                bot.join(args.pin)
            except RequestFailed as exc:
                say(f"  {bot.name}: {exc}")
        say(f"  {sum(1 for b in bots if b.session.identity)} bots are in room {args.pin}. Ctrl+C to stop them.")
        try:
            while not all(b.finished.is_set() for b in bots if b.session.identity):
                time.sleep(0.5)
            for bot in bots:
                say(f"    {bot.name}: {bot.final_score()} points")
        except KeyboardInterrupt:
            pass
        finally:
            for bot in bots:
                bot.close()
        return
    title, questions = load_quiz(args.quiz)
    say(f"Running {args.rooms} room(s) with {args.players} players each on {args.server}")
    for k, summary in enumerate(run_rooms(args.server, args.port, args.rooms, args.players, title, questions)):
        if summary is None:
            say(f"  room {k + 1}: FAILED")
            continue
        top = summary["leaderboard"][0]
        winners = ", ".join(p["username"] for p in top["players"])
        say(f"  room {k + 1} (PIN {summary['pin']}): finished; winner {winners} with {top['score']}; "
            f"{summary['answers_accepted']} answers, {summary['live_snapshots']} UDP snapshots")


if __name__ == "__main__":
    main()
