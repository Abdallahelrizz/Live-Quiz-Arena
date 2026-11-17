"""The server's UDP side (report section 2.6).

UDP carries only LIVE_STATUS: the countdown and how many players have
answered, about once per second. Each snapshot fully replaces the previous
one, so a lost datagram is simply superseded by the next. Nothing that
affects scores, answers or the phase ever travels over UDP.

One UDP socket serves every room. One thread receives on it. The only
datagram a client sends is UDP_HELLO, which proves which UDP address belongs
to which TCP session:

    client --TCP--> REQUEST_UDP_BIND
    server --TCP--> RESULT {binding_id, udp_nonce, server_udp_port}
    client --UDP--> UDP_HELLO {nonce}          (may be lost: retried by the client)
    server --TCP--> UDP_BOUND {binding_id}     (reliable confirmation)
    server --UDP--> LIVE_STATUS ...            (only after the confirmation)

Anything else that arrives on the UDP port is dropped without a reply, so the
server cannot be used to send traffic to a third party.

Shared data: `bindings` (binding_id -> Member), protected by its own small
lock. That lock is taken last and never held while taking another lock.
"""

from __future__ import annotations

import socket
import threading
from typing import Dict

from fatrat.common import log
from fatrat.protocol import udp


class UdpService:
    def __init__(self, ip: str, port: int, impair: bool = False):
        """Bind the UDP socket to the advertised address, so replies come from the address clients expect.

        impair=True is a test switch (server option --impair-udp). A real
        network rarely loses or reorders datagrams on a quiet LAN, so to
        SHOW that the client copes, the server then misbehaves on purpose:
        for each client it drops every 4th datagram, sends every 5th twice,
        and holds every 7th back until after the next one.
        """
        self.impair = impair
        self._counts: Dict[tuple, int] = {}
        self._held: Dict[tuple, bytes] = {}
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((ip, port))
        self.port = self.sock.getsockname()[1]
        self._lock = threading.Lock()
        self._bindings: Dict[str, object] = {}

    def register(self, binding_id: str, member) -> None:
        with self._lock:
            self._bindings[binding_id] = member

    def forget(self, binding_id: str) -> None:
        with self._lock:
            self._bindings.pop(binding_id, None)

    def send(self, payload: bytes, addr) -> None:
        """Send one datagram. A failure is logged and ignored: the next snapshot replaces it."""
        out = [payload]
        if self.impair:
            with self._lock:
                n = self._counts[addr] = self._counts.get(addr, 0) + 1
                held = self._held.pop(addr, None)
                if n % 4 == 0:
                    out = []                              # lost
                elif n % 5 == 0:
                    out = [payload, payload]              # duplicated
                elif n % 7 == 0:
                    self._held[addr], out = payload, []   # delayed: goes out after the next one
                if held is not None:
                    out.append(held)                      # the delayed one arrives out of order
        try:
            for datagram in out:
                self.sock.sendto(datagram, addr)
        except OSError as exc:
            log("UDP send failed:", exc)

    def recv_loop(self) -> None:
        """UDP receiver thread: accept UDP_HELLO, drop everything else silently."""
        while True:
            try:
                # The buffer is larger than any valid datagram, so an oversized
                # one is received whole and can be rejected, not silently cut.
                payload, addr = self.sock.recvfrom(udp.RECV_BUFFER)
            except OSError:
                return                                # socket closed: the server is stopping
            message, verdict = udp.parse_datagram(payload, from_server=False)
            if message is None or message["type"] != "UDP_HELLO":
                log(addr[0], f"UDP datagram dropped ({verdict})")
                continue
            with self._lock:
                member = self._bindings.get(message["binding_id"])
            if member is None:
                log(addr[0], "UDP_HELLO for an unknown binding dropped")
                continue
            problem = member.room.confirm_udp(member, message, addr)   # takes the room lock
            if problem:
                log(addr[0], f"UDP_HELLO dropped: {problem}")
            else:
                log(addr[0], f"UDP_HELLO from port {addr[1]} verified -> UDP_BOUND over TCP")

    def close(self) -> None:
        self.sock.close()
