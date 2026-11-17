"""Small helpers shared by the Fat-Rat server, the clients and the test tools.

Nothing here touches the network except detect_lan_ip(), which only asks the
operating system which local address it would use.
"""

from __future__ import annotations

import base64
import os
import socket
import threading
import time
import uuid

TCP_PORT = 43000   # default TCP port: every command, reply and STATE
UDP_PORT = 43001   # default UDP port: UDP_HELLO in, LIVE_STATUS out

_print_lock = threading.Lock()


def log(*parts) -> None:
    """Print one timestamped line. The lock stops lines from different threads mixing."""
    with _print_lock:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


def say(text: str = "") -> None:
    """Print one line without a timestamp (banners and prompts)."""
    with _print_lock:
        print(text, flush=True)


def new_id() -> str:
    """A random UUID version 4 string: the protocol's opaque ID format."""
    return str(uuid.uuid4())


def new_token() -> str:
    """32 random bytes as unpadded URL-safe Base64 (43 characters).

    Used for the host's saved identity and for player reconnect tokens.
    os.urandom() is the operating system's cryptographic random source.
    """
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def now_ms() -> int:
    """Monotonic clock in whole milliseconds (report section 2.4).

    A monotonic clock never jumps when the wall clock is changed, so
    question deadlines cannot be shortened or extended by a clock update.
    """
    return time.monotonic_ns() // 1_000_000


def detect_lan_ip() -> str:
    """Return this machine's LAN IPv4 address.

    connect() on a UDP socket sends nothing; it only asks the operating
    system which local address it would use to reach that destination.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))   # documentation-only address, never contacted
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()
