"""Fat-Rat host in a terminal. This is also the shared screen: it shows the question text.

    python3 -m fatrat.client.terminal_host --server 192.168.1.35 --quiz quizzes/networks.json

Commands (type one and press Enter):

    load <file>   load a quiz file (only before starting)
    questions     list the questions with their numbers
    append <file> add the questions of a file after the last question (also during play)
    edit          change the question that is on screen now: it pauses, and once saved
                  it restarts with its full time and the answers given so far are dropped
    edit <n>      change question n (only questions not played yet)
    move <n> <m>  move question n to position m (only questions not played yet)
    delete <n>    delete question n (only questions not played yet)
    start         start the quiz (needs 5 connected players)
    pause         freeze the clock          resume   continue
    close         close the question now and show results
    next          next question, or the final ranking after the last one
    players       list players              kick <name>   remove a player
    end           close the room for everyone
    quit          close this window; the room is kept and you can come back

The host is recognised by a saved identity (storage.py), not by an account.
A host can have one unfinished room at a time: starting this program again
returns to that room instead of creating a second one.

Because this window is the shared screen, the editor never prints which
choice is correct; Enter keeps the current correct choice.
"""

from __future__ import annotations

import argparse
import threading
from typing import Optional

from fatrat.client.core import ClientSession, NotConnected, RequestFailed
from fatrat.client.quizfile import QuizFileError, load_quiz, make_question
from fatrat.client.storage import DEFAULT_PATH, Profile
from fatrat.client.terminal_common import Screen, explain
from fatrat.common import TCP_PORT, say

HELP = ("  Commands: load <file>, questions, append <file>, edit, edit <n>, move <n> <m>, delete <n>,\n"
        "            start, pause, resume, close, next, players, kick <name>, end, quit")


def attach(session: ClientSession, profile: Profile, server: str, name: str) -> None:
    """Return to this host's unfinished room, or create a new one.

    The CREATE_ROOM request ID is saved before sending. If the reply was lost
    last time, the same request goes out again and the server answers
    ROOM_ALREADY_CREATED with the room it made, so no second room appears.
    """
    token = profile.host_token(server)
    try:
        session.create_room(name, token, request_id=profile.pending_create(server, name))
        say("  Room created.")
    except RequestFailed as exc:
        if exc.code not in ("HOST_HAS_ROOM", "ROOM_ALREADY_CREATED"):
            raise
        # This host already has a room: either unfinished, or created by this very request earlier.
        try:
            session.resume(exc.data["existing_room_id"], token)
        except RequestFailed:
            raise exc
        if session.state["phase"] == "FINISHED":
            # The saved request belongs to a room that is over: make a genuinely new one.
            profile.clear_pending(server)
            session.new_connection()
            session.create_room(name, token, request_id=profile.pending_create(server, name))
            say("  Room created.")
        else:
            say("  You already have an unfinished room. Returned to it.")
    profile.clear_pending(server)


def load(session: ClientSession, path: str) -> None:
    try:
        title, questions = load_quiz(path)
        session.host_command("PUT_QUIZ", title=title, questions=questions)
    except QuizFileError as exc:
        say(f"  {exc}")


# ----------------------------------------------------------------------
# The quiz as the host sees it. Only the host's STATE contains the quiz.
# ----------------------------------------------------------------------

def quiz_questions(session: ClientSession) -> list:
    quiz = session.state["quiz"]
    return quiz["questions"] if quiz else []


def is_live(state: dict) -> bool:
    """True while a question is on screen (open or paused) and can be edited with EDIT_BEGIN."""
    return state["phase"] in ("OPEN", "PAUSED") and state["question_index"] is not None


def first_editable(state: dict) -> int:
    """Position of the first question that may still change (the server applies the same rule)."""
    if state["phase"] in ("LOBBY", "FINISHED") or state["question_index"] is None:
        return 0
    return state["question_index"] + 1


def list_questions(session: ClientSession) -> None:
    state, questions = session.state, quiz_questions(session)
    if not questions:
        say("  No quiz is loaded. Use: load <file>")
        return
    start = first_editable(state)
    for number, q in enumerate(questions, start=1):
        if number - 1 >= start:
            mark = ""
        elif number - 1 == state["question_index"] and is_live(state):
            mark = "  [on screen now]"
        else:
            mark = "  [played]"
        say(f"    {number}. {q['text']}  ({len(q['choices'])} choices, {q['time_limit_ms'] / 1000:g} s){mark}")


def ask_question(q: dict, number: int) -> Optional[dict]:
    """Ask for the new content of question `number`. Enter keeps each value.

    Returns the edited question with the same IDs, or None if nothing changed
    or a typed value breaks a rule (the reason is printed).
    """
    say(f"  Editing question {number}. Press Enter to keep a value.")
    texts = [c["text"] for c in q["choices"]]
    correct = [c["choice_id"] for c in q["choices"]].index(q["correct_choice_id"])
    try:
        text = input(f"  Question [{q['text']}]: ").strip() or q["text"]
        typed = input(f"  Choices, separated by | [{' | '.join(texts)}]: ").strip()
        if typed:
            texts = [part.strip() for part in typed.split("|")]
        typed = input("  Number of the correct choice (Enter keeps the current one): ").strip()
        if typed:
            if not typed.isdigit():
                raise QuizFileError("the correct choice is a number, such as 2")
            correct = int(typed) - 1
        typed = input(f"  Seconds [{q['time_limit_ms'] / 1000:g}]: ").strip()
        try:
            seconds = float(typed) if typed else q["time_limit_ms"] / 1000
        except ValueError:
            raise QuizFileError("seconds must be a number, such as 20")
        new = make_question(text, texts, correct, seconds, q["question_id"],
                            [c["choice_id"] for c in q["choices"]])
    except QuizFileError as exc:
        say(f"  Not saved: {exc}.")
        return None
    if new == q:
        say("  Nothing changed.")
        return None
    return new


def confirm(question: str) -> bool:
    return input(question).strip().lower() == "y"


def edit_current(session: ClientSession) -> None:
    """Edit the question on screen: EDIT_BEGIN, then EDIT_COMMIT or EDIT_CANCEL.

    EDIT_BEGIN pauses the question for everyone. If the host saves, the server
    replaces it and restarts it as a new attempt with its full time; answers
    to the old version are dropped. If the host does not save, for any reason,
    EDIT_CANCEL keeps question, answers and time exactly as they were.
    Either way the question stays paused until the host types 'resume'.
    """
    state = session.state
    index = state["question_index"]
    q = quiz_questions(session)[index]
    if state["pause_reason"] != "EDITING":            # already editing after a reconnect: carry on
        session.host_command("EDIT_BEGIN", question_id=q["question_id"])
        say("  The question is paused while you edit it.")
    saved = False
    try:
        new = ask_question(q, index + 1)
        if new is not None and confirm("  Save? The answers given so far are dropped and the question "
                                       "restarts with its full time. [y/N] "):
            session.host_command("EDIT_COMMIT", question=new)
            saved = True
            say("  Saved. Type 'resume' to show the new version to the players.")
    finally:
        if not saved:
            try:
                session.host_command("EDIT_CANCEL")
                say("  Edit cancelled: the question, answers and time are unchanged. Type 'resume' to continue.")
            except (RequestFailed, NotConnected):
                pass                                  # the edit stays open on the server; 'edit' continues it


def edit_numbered(session: ClientSession, number: int) -> None:
    """Edit question `number` (counting from 1): the current one, or one not played yet."""
    state, questions = session.state, quiz_questions(session)
    if not 1 <= number <= len(questions):
        say(f"  The quiz has questions 1 to {len(questions)}." if questions else "  No quiz is loaded.")
        return
    if is_live(state) and number - 1 == state["question_index"]:
        edit_current(session)
        return
    if number - 1 < first_editable(state):
        say("  That question has already been played and cannot be changed.")
        return
    new = ask_question(questions[number - 1], number)
    if new is not None and confirm("  Save? [y/N] "):
        session.host_command("EDIT_QUESTION", question=new)
        say(f"  Question {number} saved.")


def move(session: ClientSession, source: int, target: int) -> None:
    """Move question `source` to position `target` with REORDER_QUESTIONS.

    The server takes the new order of every question that may still move,
    by ID, and refuses any other list, so played questions can never move.
    """
    questions = quiz_questions(session)
    start = first_editable(session.state)
    if start >= len(questions):
        say("  No question is left that can be moved.")
        return
    if not (start < source <= len(questions) and start < target <= len(questions)):
        say(f"  Only questions {start + 1} to {len(questions)} can still be moved.")
        return
    order = [q["question_id"] for q in questions[start:]]
    order.insert(target - 1 - start, order.pop(source - 1 - start))
    session.host_command("REORDER_QUESTIONS", question_ids=order)
    say(f"  Question {source} is now question {target}.")


def run_command(session: ClientSession, line: str) -> bool:
    """Run one typed command. Returns False when the program should end."""
    word, _, rest = line.partition(" ")
    word, rest = word.lower(), rest.strip()
    simple = {"start": "START_QUIZ", "pause": "PAUSE_QUIZ", "resume": "RESUME_QUIZ",
              "close": "CLOSE_QUESTION", "next": "NEXT_QUESTION"}
    if word == "quit":
        say("  Closed. The room is kept; run the program again to return to it.")
        return False
    if word in simple:
        session.host_command(simple[word])
    elif word == "load" and rest:
        load(session, rest)
    elif word == "questions":
        list_questions(session)
    elif word == "append" and rest:
        try:
            _, questions = load_quiz(rest)
            for question in questions:                 # one ADD_QUESTION each; null = after the last
                session.host_command("ADD_QUESTION", question=question, before_question_id=None)
            say(f"  Added {len(questions)} question(s). The quiz now has {session.state['question_count']}.")
        except QuizFileError as exc:
            say(f"  {exc}")
    elif word == "edit" and not rest:
        if is_live(session.state):
            edit_current(session)
        else:
            say("  No question is on screen. Use 'edit <n>' for a question not played yet.")
    elif word == "edit" and rest.isdigit():
        edit_numbered(session, int(rest))
    elif word == "move" and len(rest.split()) == 2 and all(part.isdigit() for part in rest.split()):
        source, target = (int(part) for part in rest.split())
        move(session, source, target)
    elif word == "delete" and rest.isdigit():
        questions = quiz_questions(session)
        if not 1 <= int(rest) <= len(questions):
            say(f"  The quiz has questions 1 to {len(questions)}.")
        else:
            session.host_command("DELETE_QUESTION", question_id=questions[int(rest) - 1]["question_id"])
            say(f"  Question {rest} deleted.")
    elif word == "players":
        for p in session.state["participants"]:
            status = "online" if p["connected"] else "offline"
            if p["participation"] != "ACTIVE":
                status = p["participation"].lower()
            say(f"    {p['username']}  {p['total_score']} points  ({status})")
    elif word == "kick" and rest:
        match = [p for p in session.state["participants"]
                 if p["username"].casefold() == rest.casefold() and p["participation"] == "ACTIVE"]
        if not match:
            say(f"  No active player is called {rest!r}.")
        else:
            session.host_command("REMOVE_PLAYER", player_id=match[0]["player_id"])
            say(f"  Removed {match[0]['username']}.")
    elif word == "end":
        if confirm("  End the room for everyone? An open question will not be scored. [y/N] "):
            session.host_command("CLOSE_ROOM")
    elif word:
        say(HELP)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat host (terminal).")
    parser.add_argument("--server", required=True, help="server IPv4 address shown by the server")
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("--name", default="Host")
    parser.add_argument("--quiz", help="quiz file to load at once")
    parser.add_argument("--profile", default=DEFAULT_PATH, help="file holding the saved host identity")
    args = parser.parse_args()
    server = f"{args.server}:{args.port}"
    profile = Profile(args.profile)
    session = ClientSession(args.server, args.port)
    session.on_event = Screen(session, "HOST").on_event
    say("Fat-Rat host")
    try:
        session.connect()
    except OSError as exc:
        say(f"  Cannot reach the server at {server}: {exc}")
        return
    try:
        attach(session, profile, server, args.name)
        say(f"\n  >>> Room PIN: {session.identity['pin']}   (players join {args.server} with this PIN) <<<\n")
        threading.Thread(target=session.enable_udp, daemon=True).start()
        if args.quiz and session.state["phase"] == "LOBBY":
            load(session, args.quiz)
        say(HELP)
        while True:
            line = input().strip()
            try:
                if not run_command(session, line):
                    break
            except RequestFailed as exc:
                say(f"  {explain(exc)}")
            except NotConnected as exc:
                say(f"  No answer from the server ({exc}). Try again in a moment.")
    except RequestFailed as exc:
        say(f"  {explain(exc)}")
    except NotConnected as exc:
        # For example the reply to CREATE_ROOM was lost. The request was saved,
        # so running the program again returns to the room it may have created.
        say(f"  No answer from the server ({exc}). Run the program again: it returns to the same room.")
    except (EOFError, KeyboardInterrupt):
        say("")
    finally:
        session.auto_reconnect = False
        session.close()


if __name__ == "__main__":
    main()
