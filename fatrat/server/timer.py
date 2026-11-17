"""The room timer: one thread per room.

It does two jobs, about once per second and exactly at a question's deadline:

  1. closes the open question when its time is up, so results appear even if
     no client sends anything at that moment;
  2. sends one LIVE_STATUS datagram over UDP to every registered client.

The timer is not what makes late answers impossible. Every request handler
also checks the deadline itself (Room._expire), so correctness does not
depend on this thread waking up on time. The timer only makes the close
happen promptly.

All room state is read inside Room.tick(), under the room lock. The
datagrams are sent here, after the lock has been released, because a socket
call must never run while a lock is held.
"""

from __future__ import annotations

import threading

from fatrat.server.room import Room

LIVE_INTERVAL = 1.0   # seconds between LIVE_STATUS snapshots


def run(room: Room, udp_service, interval: float = LIVE_INTERVAL) -> None:
    """Body of a room's timer thread. Returns when the room has finished."""
    while True:
        step = room.tick(interval)
        if step is None:
            return
        datagrams, wait = step
        for payload, addr in datagrams:
            udp_service.send(payload, addr)
        # Sleep until the next tick, or less if the room sets `wake`
        # (a question opened or resumed, so the deadline changed).
        room.wake.wait(wait)


def start(room: Room, udp_service, interval: float = LIVE_INTERVAL) -> threading.Thread:
    thread = threading.Thread(target=run, args=(room, udp_service, interval),
                              name=f"timer {room.pin}", daemon=True)
    thread.start()
    return thread
