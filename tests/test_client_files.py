"""Tests of what the client keeps on disk: the saved identity file and quiz files.

No network. Each test works in its own temporary folder.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from fatrat.client.quizfile import QuizFileError, load_quiz, make_question, save_quiz
from fatrat.client.storage import Profile

HERE = os.path.dirname(os.path.abspath(__file__))
QUIZZES = os.path.join(os.path.dirname(HERE), "quizzes")


class TempFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = folder.name

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)


class ProfileTests(TempFolder):
    def test_host_identity_survives_a_restart_and_differs_per_server(self):
        file = self.path("profile.json")
        token = Profile(file).host_token("10.0.0.5:43000")
        self.assertEqual(Profile(file).host_token("10.0.0.5:43000"), token)     # a new run reads it back
        self.assertNotEqual(Profile(file).host_token("10.0.0.9:43000"), token)
        self.assertGreaterEqual(len(token), 43)                                  # 32 random bytes

    def test_pending_create_is_saved_before_sending_and_reused_until_cleared(self):
        file, server = self.path("profile.json"), "10.0.0.5:43000"
        first = Profile(file).pending_create(server, "Teacher")
        self.assertEqual(Profile(file).pending_create(server, "Teacher"), first)  # crash, rerun: same request
        renamed = Profile(file).pending_create(server, "Someone else")            # different content: new request
        self.assertNotEqual(renamed, first)
        profile = Profile(file)
        profile.clear_pending(server)
        self.assertNotIn(profile.pending_create(server, "Someone else"), (first, renamed))

    def test_room_and_reconnect_token_are_remembered_and_forgotten(self):
        file, server = self.path("profile.json"), "10.0.0.5:43000"
        identity = {"room_id": "r", "pin": "123456", "reconnect_token": "t"}
        Profile(file).remember(server, "PLAYER", identity, "Rayan")
        self.assertEqual(Profile(file).saved(server, "PLAYER"),
                         {"room_id": "r", "pin": "123456", "reconnect_token": "t", "username": "Rayan"})
        self.assertIsNone(Profile(file).saved(server, "HOST"))
        Profile(file).forget(server, "PLAYER")
        self.assertIsNone(Profile(file).saved(server, "PLAYER"))

    def test_missing_or_damaged_file_starts_empty_instead_of_crashing(self):
        file = self.path("profile.json")
        with open(file, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        profile = Profile(file)
        self.assertIsNone(profile.saved("x", "PLAYER"))
        profile.host_token("x")                                                   # rewrites a valid file
        with open(file, encoding="utf-8") as handle:
            self.assertIn("x", json.load(handle)["hosts"])
        self.assertIsNone(Profile(self.path("sub/new.json")).saved("x", "HOST"))
        self.assertFalse(os.path.exists(file + ".tmp"))                          # the temporary file was renamed


class QuizFileTests(TempFolder):
    def write(self, content) -> str:
        file = self.path("quiz.json")
        with open(file, "w", encoding="utf-8") as handle:
            handle.write(content if isinstance(content, str) else json.dumps(content))
        return file

    def test_sample_quizzes_load(self):
        for name in ("networks.json", "quick-demo.json"):
            title, questions = load_quiz(os.path.join(QUIZZES, name))
            self.assertTrue(title)
            self.assertGreaterEqual(len(questions), 1)

    def test_save_then_load_keeps_the_content(self):
        title, questions = load_quiz(os.path.join(QUIZZES, "networks.json"))
        save_quiz(self.path("copy.json"), title, questions)
        again_title, again = load_quiz(self.path("copy.json"))
        self.assertEqual(again_title, title)
        plain = lambda qs: [(q["text"], [c["text"] for c in q["choices"]], q["time_limit_ms"],
                             [c["choice_id"] for c in q["choices"]].index(q["correct_choice_id"])) for q in qs]
        self.assertEqual(plain(again), plain(questions))                         # IDs are new, content is equal

    def test_bad_files_are_explained(self):
        good = {"text": "Q?", "choices": ["a", "b"], "correct": 0, "time_seconds": 10}
        cases = {
            "missing": None,
            "not json": "{",
            "wrong layout": {"title": "T"},
            "correct out of range": {"title": "T", "questions": [{**good, "correct": 2}]},
            "one choice": {"title": "T", "questions": [{**good, "choices": ["a"], "correct": 0}]},
            "too long": {"title": "T", "questions": [{**good, "time_seconds": 301}]},
            "no questions": {"title": "T", "questions": []},
        }
        for case, content in cases.items():
            with self.subTest(case):
                file = self.path("absent.json") if content is None else self.write(content)
                with self.assertRaises(QuizFileError):
                    load_quiz(file)

    def test_make_question_keeps_ids_when_editing(self):
        original = make_question("Old?", ["a", "b"], 0, 20)
        ids = [c["choice_id"] for c in original["choices"]]
        edited = make_question("New?", ["a", "b", "c"], 2, 15, original["question_id"], ids)
        self.assertEqual(edited["question_id"], original["question_id"])
        self.assertEqual([c["choice_id"] for c in edited["choices"]][:2], ids)   # existing choices keep their IDs
        self.assertEqual(edited["correct_choice_id"], edited["choices"][2]["choice_id"])
        self.assertEqual(edited["time_limit_ms"], 15_000)
        with self.assertRaises(QuizFileError):
            make_question("Q?", ["a", "b"], 5, 20)                               # correct must be a choice
        with self.assertRaises(QuizFileError):
            make_question("Q?", ["a", "b"], 0, 0)                                # 1 to 300 seconds


if __name__ == "__main__":
    unittest.main()
