"""Small local store for what a client must remember between runs.

  * The host's saved identity (host_token). It is created once per server
    address and reused for every room, which is how the server knows that
    two CREATE_ROOM requests come from the same host without any account.
  * The room a client is in and its reconnect token, so that after a crash
    or a restart the client can come back as the same host or player.

It is one JSON file, by default ~/.fatrat/profile.json. Tokens are secrets:
they are kept only in this file and never printed or sent anywhere except to
the server over TCP.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from fatrat.common import new_id, new_token

DEFAULT_PATH = os.path.join(os.path.expanduser("~"), ".fatrat", "profile.json")


class Profile:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._data = {"hosts": {}, "sessions": {}, "pending": {}}
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except (OSError, ValueError):
            pass                                  # no file yet, or unreadable: start empty

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2)
        os.replace(temporary, self.path)          # replace in one step: never a half-written file

    def host_token(self, server: str) -> str:
        """The saved host identity for this server address; created on first use."""
        token = self._data["hosts"].get(server)
        if token is None:
            token = self._data["hosts"][server] = new_token()
            self._save()
        return token

    def pending_create(self, server: str, username: str) -> str:
        """The request_id to use for CREATE_ROOM, saved BEFORE the request is sent.

        If the reply is lost (crash, network drop), the next run sends the
        same request with the same ID. The server recognises it and answers
        ROOM_ALREADY_CREATED with the room's ID instead of making a second
        room. A different host name starts a fresh request.
        """
        saved = self._data["pending"].get(server)
        if saved is None or saved["username"] != username:
            saved = self._data["pending"][server] = {"request_id": new_id(), "username": username}
            self._save()
        return saved["request_id"]

    def clear_pending(self, server: str) -> None:
        """The room is known (created or resumed): the next create is a new request."""
        if self._data["pending"].pop(server, None) is not None:
            self._save()

    def remember(self, server: str, role: str, identity: dict, username: str) -> None:
        """Save the room and reconnect token after create, join or resume."""
        self._data["sessions"][f"{server}|{role}"] = {
            "room_id": identity["room_id"], "pin": identity["pin"],
            "reconnect_token": identity["reconnect_token"], "username": username}
        self._save()

    def saved(self, server: str, role: str) -> Optional[dict]:
        return self._data["sessions"].get(f"{server}|{role}")

    def forget(self, server: str, role: str) -> None:
        """Drop the saved room (after leaving, being removed, or when the room no longer exists)."""
        if self._data["sessions"].pop(f"{server}|{role}", None) is not None:
            self._save()
