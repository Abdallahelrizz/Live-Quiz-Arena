"""Quiz files: the simple data source the questions are loaded from.

A quiz file is JSON that a person can write by hand:

    {
      "title": "Computer Networks",
      "questions": [
        {"text": "Which transport is connection-oriented?",
         "choices": ["TCP", "UDP", "ICMP", "ARP"],
         "correct": 0,
         "time_seconds": 20}
      ]
    }

"correct" is the position of the right choice, starting at 0. A question has
2 to 8 choices and 1 to 300 seconds; a quiz has 1 to 30 questions.

The protocol identifies questions and choices by IDs, not by position, so
load_quiz() gives every question and choice a fresh UUID and returns the
quiz in the protocol's own form, ready for PUT_QUIZ. It is validated with
the same function the server uses, so a bad file is reported before
anything is sent.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence, Tuple

from fatrat.common import new_id
from fatrat.protocol.messages import ProtocolError, validate_question, validate_quiz


class QuizFileError(Exception):
    """The file is missing, is not valid JSON, or breaks a quiz rule. The text says which."""


def make_question(text: str, choice_texts: Sequence[str], correct: int, seconds: float,
                  question_id: Optional[str] = None, choice_ids: Sequence[str] = ()) -> dict:
    """Build one question in protocol form from plain values, and check it.

    correct is the position of the right choice, starting at 0. When a
    question is edited, question_id and choice_ids keep its existing IDs,
    because the protocol finds a question by its ID, not by its position;
    a choice without an existing ID gets a new one. The result is checked
    with validate_question, the server's own rule, so a mistake is reported
    before anything is sent. Raises QuizFileError.
    """
    choices = [{"choice_id": choice_ids[i] if i < len(choice_ids) else new_id(), "text": str(t)}
               for i, t in enumerate(choice_texts)]
    if type(correct) is not int or not 0 <= correct < len(choices):
        raise QuizFileError("the correct answer must be one of the choices")
    question = {"question_id": question_id or new_id(), "text": str(text), "choices": choices,
                "correct_choice_id": choices[correct]["choice_id"],
                "time_limit_ms": int(round(float(seconds) * 1000))}
    try:
        validate_question(question)
    except ProtocolError as exc:
        raise QuizFileError(exc.detail) from exc
    return question


def load_quiz(path: str) -> Tuple[str, list]:
    """Read a quiz file. Returns (title, questions) in protocol form. Raises QuizFileError."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except OSError as exc:
        raise QuizFileError(f"cannot read {path}: {exc.strerror}") from exc
    except ValueError as exc:
        raise QuizFileError(f"{path} is not valid JSON: {exc}") from exc
    try:
        title = raw["title"]
        questions = []
        for number, item in enumerate(raw["questions"], start=1):
            try:
                questions.append(make_question(item["text"], item["choices"], item["correct"],
                                               item.get("time_seconds", 20)))
            except QuizFileError as exc:
                raise QuizFileError(f"{path}, question {number}: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise QuizFileError(f"{path} does not have the quiz layout (missing or wrong {exc})") from exc
    try:
        validate_quiz(title, questions)
    except ProtocolError as exc:
        raise QuizFileError(f"{path}: {exc.detail}") from exc
    return title, questions


def save_quiz(path: str, title: str, questions: list) -> None:
    """Write a quiz (protocol form) back to a hand-editable file."""
    plain = {"title": title, "questions": [
        {"text": q["text"], "choices": [c["text"] for c in q["choices"]],
         "correct": [c["choice_id"] for c in q["choices"]].index(q["correct_choice_id"]),
         "time_seconds": q["time_limit_ms"] / 1000}
        for q in questions]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(plain, handle, indent=2, ensure_ascii=False)
