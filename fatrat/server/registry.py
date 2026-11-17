"""The registry: every room on the server, and which host owns which room.

Shared data and its lock
------------------------
One registry lock protects four tables:

  by_pin   PIN -> room            players find a room with the PIN
  by_id    room_id -> room        reconnecting clients find it with the room ID
  owners   host key -> room       the host's one unfinished room
  ledger   (host key, request_id) -> (request fingerprint, room_id)
           every CREATE_ROOM that succeeded, so a repeated request is recognised

CREATE_ROOM holds the lock for the whole check-and-create step. Without it,
two CREATE_ROOM requests from the same host arriving together could both see
"no room yet" and create two rooms, or two rooms could get the same PIN.

Lock order: registry lock first, then a room lock, never the other way. A
thread that holds a room lock never asks for the registry lock, so two
threads can never wait for each other forever (no deadlock). Lookups release
the registry lock before the room is used.
"""

from __future__ import annotations

import secrets
import threading
from typing import Callable, Dict, Optional, Tuple

from fatrat.common import log
from fatrat.protocol.framing import encode_json
from fatrat.server.connection import error
from fatrat.server.room import MAX_NAME, Room, clean_name, token_key

# Requests that can finish a room and so release the host's ownership.
# They run with the registry lock held (see handle()).
FINISHING_REQUESTS = frozenset({"NEXT_QUESTION", "CLOSE_ROOM"})
MAX_ROOMS = 500   # rooms are kept in memory until the server stops, so their number is bounded


class Registry:
    def __init__(self, udp_service, start_timer: Callable[[Room], None]):
        """udp_service is passed to every room; start_timer(room) starts a room's timer thread."""
        self.lock = threading.Lock()
        self.by_pin: Dict[str, Room] = {}
        self.by_id: Dict[str, Room] = {}
        self.owners: Dict[str, Room] = {}
        self.ledger: Dict[Tuple[str, str], Tuple[bytes, str]] = {}
        self._udp = udp_service
        self._start_timer = start_timer

    def create(self, conn, msg: dict) -> str:
        """CREATE_ROOM. Returns a short text for the server log."""
        data, rid = msg["data"], msg["request_id"]
        name = clean_name(data["username"])
        if name is None:
            conn.send(error("INVALID_USERNAME", f"names must be 1..{MAX_NAME} characters", rid))
            return "INVALID_USERNAME"
        key = token_key(data["host_token"])
        fingerprint = encode_json(data)
        with self.lock:
            earlier = self.ledger.get((key, rid))
            if earlier is not None:
                # This exact request already created a room (its reply was probably lost).
                if earlier[0] != fingerprint:
                    conn.send(error("REQUEST_ID_REUSED", "request_id reused with different content", rid))
                    return "REQUEST_ID_REUSED"
                conn.send(error("ROOM_ALREADY_CREATED", "this request already created a room", rid,
                                existing_room_id=earlier[1]))
                return "ROOM_ALREADY_CREATED"
            owned = self.owners.get(key)
            if owned is not None:
                conn.send(error("HOST_HAS_ROOM", "finish or close your current room first", rid,
                                existing_room_id=owned.room_id))
                return f"HOST_HAS_ROOM (room {owned.pin})"
            if len(self.by_id) >= MAX_ROOMS:          # checked last, so lost-reply recovery above still works
                conn.send(error("SERVER_BUSY", "the server holds too many rooms; restart it", rid))
                return "SERVER_BUSY"
            room = Room(self._free_pin(), name, data["host_token"],
                        on_finish=self._release, udp_service=self._udp)
            self.by_pin[room.pin] = room
            self.by_id[room.room_id] = room
            self.owners[key] = room
            self.ledger[(key, rid)] = (fingerprint, room.room_id)
            room.attach_host(conn, rid)               # registry lock, then room lock
        self._start_timer(room)
        return f"{name!r} created room {room.pin}"

    def _free_pin(self) -> str:
        """A six-digit PIN no other room uses. Caller holds the lock."""
        while True:
            pin = f"{secrets.randbelow(1_000_000):06d}"
            if pin not in self.by_pin:
                return pin

    def _release(self, room: Room) -> None:
        """A room finished: its host may create another. Caller holds the registry lock.

        Only this room's ownership is removed, so a late action on an old
        room can never free the slot of the host's newer room.
        """
        if self.owners.get(room.host_key) is room:
            del self.owners[room.host_key]
            log(f"room {room.pin}", "finished; its host may create a new room")

    def find_pin(self, pin: str) -> Optional[Room]:
        with self.lock:
            return self.by_pin.get(pin)

    def find_id(self, room_id: str) -> Optional[Room]:
        with self.lock:
            return self.by_id.get(room_id)

    def handle(self, conn, room: Room, msg: dict) -> str:
        """Run one request of an attached connection with the right locks."""
        if msg["type"] in FINISHING_REQUESTS:
            with self.lock:                           # registry lock first, room lock inside
                return room.handle(conn, msg)
        return room.handle(conn, msg)

    def room_count(self) -> int:
        with self.lock:
            return len(self.by_id)
