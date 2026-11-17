"""Fat-Rat player in a terminal.

    python3 -m fatrat.client.terminal_player --server 192.168.1.35

Asks for the room PIN and a name, then shows each question as numbered
cheese symbols. Type the number of your answer and press Enter. Type
'leave' to leave the room for good, or 'quit' to close this window while
keeping your place (run the program again to come back).

All networking is in ClientSession (core.py); this file only reads the
keyboard and prints.
"""

from __future__ import annotations

import argparse
import threading

from fatrat.client.core import ClientSession, NotConnected, RequestFailed
from fatrat.client.storage import DEFAULT_PATH, Profile
from fatrat.client.terminal_common import Screen, explain
from fatrat.common import TCP_PORT, say


def attach(session: ClientSession, profile: Profile, server: str, args) -> str:
    """Rejoin the saved room if there is one, otherwise ask for PIN and name. Returns the username."""
    saved = profile.saved(server, "PLAYER")
    if saved and not args.pin:
        answer = input(f"  Rejoin room {saved['pin']} as {saved['username']}? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            try:
                session.resume(saved["room_id"], saved["reconnect_token"])
                return saved["username"]
            except RequestFailed as exc:
                say(f"  Could not rejoin ({exc.code}). Join a room instead.")
        profile.forget(server, "PLAYER")
    while True:
        pin = args.pin or input("  Room PIN: ").strip()
        name = args.name or input("  Your name: ").strip()
        try:
            session.join(pin, name)
            return name
        except RequestFailed as exc:
            say(f"  {explain(exc)}")
            args.pin = args.name = None            # ask again


def main() -> None:
    parser = argparse.ArgumentParser(description="Fat-Rat player (terminal).")
    parser.add_argument("--server", required=True, help="server IPv4 address shown by the server")
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("--pin")
    parser.add_argument("--name")
    parser.add_argument("--profile", default=DEFAULT_PATH, help="file that remembers your room for rejoining")
    args = parser.parse_args()
    server = f"{args.server}:{args.port}"
    profile = Profile(args.profile)
    session = ClientSession(args.server, args.port)
    screen = Screen(session, "PLAYER")
    done = threading.Event()

    def on_event(kind, payload):
        screen.on_event(kind, payload)
        if kind == "notice":                       # removed, replaced or refused: nothing more to do
            profile.forget(server, "PLAYER")
            done.set()

    session.on_event = on_event
    say("Fat-Rat player")
    try:
        session.connect()
    except OSError as exc:
        say(f"  Cannot reach the server at {server}: {exc}")
        return
    try:
        name = attach(session, profile, server, args)
        profile.remember(server, "PLAYER", session.identity, name)
        # UDP registration runs beside the game: if it fails, only the live countdown is missing.
        threading.Thread(target=session.enable_udp, daemon=True).start()
        while not done.is_set():
            line = input().strip().lower()
            if done.is_set():
                break
            if line == "quit":
                say("  Closed. Run the program again to come back to the same place.")
                break
            if line == "leave":
                if input("  Leave for good? You cannot come back under this name. [y/N] ").strip().lower() == "y":
                    session.auto_reconnect = False
                    session.request("LEAVE_ROOM", {})
                    profile.forget(server, "PLAYER")
                    say("  You left the room.")
                    break
                continue
            state = session.state or {}
            question = state.get("current_question")
            if line.isdigit() and question and state["phase"] in ("OPEN", "PAUSED"):
                number = int(line)
                if not 1 <= number <= len(question["choices"]):
                    say(f"  Type a number from 1 to {len(question['choices'])}.")
                    continue
                try:
                    session.submit_answer(question["choices"][number - 1]["choice_id"])
                    say("  Answer received by the server. Waiting for the result...")
                except RequestFailed as exc:
                    say(f"  {explain(exc)}")
            elif line:
                say("  Type the number of your answer, 'leave' or 'quit'.")
    except NotConnected as exc:
        say(f"  No answer from the server ({exc}). It will keep trying to reconnect; try again.")
    except (EOFError, KeyboardInterrupt):
        say("")
    finally:
        session.auto_reconnect = False
        session.close()


if __name__ == "__main__":
    main()
