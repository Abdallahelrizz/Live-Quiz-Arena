"""Helpers shared by the protocol check server and client."""

from __future__ import annotations

import base64
import os
import socket
import threading
import time
import uuid

TCP_PORT = 43000
UDP_PORT = 43001

_print_lock = threading.Lock()


def log(*parts) -> None:
    """Print one timestamped line; the lock stops lines from different threads mixing."""
    with _print_lock:
        print(time.strftime("%H:%M:%S"), *parts, flush=True)


def say(text: str = "") -> None:
    with _print_lock:
        print(text, flush=True)


def new_id() -> str:
    return str(uuid.uuid4())


def new_token() -> str:
    """32 random bytes as unpadded URL-safe Base64 (43 characters)."""
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def now_ms() -> int:
    """Monotonic clock in integer milliseconds (report section 2.4)."""
    return time.monotonic_ns() // 1_000_000


def score(remaining_ms: int, limit_ms: int) -> int:
    """Points for a correct answer: 500..1000 depending on remaining time (section 2.4)."""
    r = max(0, min(remaining_ms, limit_ms))
    return 500 + (1000 * r + limit_ms) // (2 * limit_ms)


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
