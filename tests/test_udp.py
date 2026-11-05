"""Tests for UDP datagrams and LIVE_STATUS handling (report sections 2.2 and 2.6).

The last test sends real datagrams over 127.0.0.1 with deliberate loss,
duplication and reordering and checks that the countdown the client keeps
never goes backwards and never uses a duplicate.
"""

from __future__ import annotations

import socket
import unittest
import uuid

from fatrat.protocol import udp
from fatrat.protocol.framing import encode_json
from fatrat.protocol.messages import MAX_INT
from tests.samples import live_status, new_id

SERVER = ("127.0.0.1", 43001)


def worst_case_live_status() -> dict:
    # Every ID a full UUID and every number at the largest allowed value.
    return live_status(control_rev=MAX_INT, seq=MAX_INT,
                       data={"remaining_ms": MAX_INT, "answered_count": MAX_INT, "eligible_count": MAX_INT})


class DatagramSizeTest(unittest.TestCase):
    def test_worst_case_live_status_fits_in_512_bytes(self):
        size = len(udp.encode_datagram(worst_case_live_status()))
        self.assertLessEqual(size, udp.MAX_DATAGRAM)
        print(f"\n    worst-case LIVE_STATUS = {size} bytes (limit {udp.MAX_DATAGRAM})", end="")

    def test_worst_case_udp_hello_fits(self):
        hello = {"v": 1, "type": "UDP_HELLO", "session_id": new_id(), "binding_id": new_id(),
                 "data": {"nonce": "f" * 32}}
        self.assertLessEqual(len(udp.encode_datagram(hello)), udp.MAX_DATAGRAM)

    def test_encoder_refuses_oversized_datagrams(self):
        with self.assertRaises(ValueError):
            udp.encode_datagram({"v": 1, "padding": "x" * 600})

    def test_parse_drops_bad_datagrams(self):
        good = udp.encode_datagram(live_status())
        cases = {
            "oversize": (encode_json({**live_status(), "pad": "x" * 600}), udp.OVERSIZE),
            "truncated": (good[:-5], udp.MALFORMED),
            "not JSON": (b"hello", udp.MALFORMED),
            "TCP-only type over UDP": (encode_json({"v": 1, "type": "STATE", "data": {}}), udp.MALFORMED),
        }
        for name, (payload, reason) in cases.items():
            with self.subTest(name):
                self.assertEqual(udp.parse_datagram(payload, from_server=True), (None, reason))
        message, verdict = udp.parse_datagram(good, from_server=True)
        self.assertEqual(verdict, udp.ACCEPTED)


class LiveStatusFilterTest(unittest.TestCase):
    def setUp(self):
        self.ids = {"room_id": new_id(), "session_id": new_id(), "question_id": new_id(), "attempt_id": new_id()}
        self.binding = new_id()
        self.filter = udp.LiveStatusFilter(SERVER)
        self.filter.confirm_binding(self.binding)
        self.filter.set_context(control_rev=3, **self.ids)

    def snapshot(self, seq, **overrides):
        message = live_status(binding_id=self.binding, control_rev=3, seq=seq, **self.ids)
        message.update(overrides)
        return encode_json(message)

    def verdict(self, payload, source=SERVER):
        return self.filter.check(payload, source)[1]

    def test_strictly_increasing_seq(self):
        self.assertEqual(self.verdict(self.snapshot(1)), udp.ACCEPTED)
        self.assertEqual(self.verdict(self.snapshot(1)), udp.DUPLICATE)
        self.assertEqual(self.verdict(self.snapshot(4)), udp.ACCEPTED)   # 2 and 3 lost: tolerated
        self.assertEqual(self.verdict(self.snapshot(3)), udp.OUT_OF_ORDER)
        self.assertEqual(self.verdict(self.snapshot(5)), udp.ACCEPTED)

    def test_rejections_that_seq_alone_would_miss(self):
        cases = {
            udp.WRONG_SOURCE: (self.snapshot(1), ("127.0.0.1", 50000)),
            udp.WRONG_BINDING: (self.snapshot(1, binding_id=new_id()), SERVER),
            udp.WRONG_SESSION: (self.snapshot(1, session_id=new_id()), SERVER),
            udp.WRONG_ATTEMPT: (self.snapshot(1, attempt_id=new_id()), SERVER),
            udp.FUTURE_REVISION: (self.snapshot(1, control_rev=4), SERVER),
            udp.OLD_REVISION: (self.snapshot(1, control_rev=2), SERVER),
        }
        for reason, (payload, source) in cases.items():
            with self.subTest(reason):
                self.assertEqual(self.verdict(payload, source), reason)
        self.assertEqual(self.verdict(self.snapshot(1)), udp.ACCEPTED)   # the real one still works

    def test_nothing_accepted_before_udp_bound_or_without_a_live_question(self):
        fresh = udp.LiveStatusFilter(SERVER)
        self.assertEqual(fresh.check(self.snapshot(1), SERVER)[1], udp.NOT_BOUND)
        fresh.confirm_binding(self.binding)
        self.assertEqual(fresh.check(self.snapshot(1), SERVER)[1], udp.NO_LIVE_QUESTION)

    def test_rebind_resets_seq_and_retires_old_binding(self):
        self.assertEqual(self.verdict(self.snapshot(9)), udp.ACCEPTED)
        new_binding = new_id()
        self.filter.confirm_binding(new_binding)
        self.assertEqual(self.verdict(self.snapshot(10)), udp.WRONG_BINDING)
        self.assertEqual(self.verdict(self.snapshot(1, binding_id=new_binding)), udp.ACCEPTED)


class RealSocketImpairmentTest(unittest.TestCase):
    """Real UDP sockets: the sender loses, duplicates and reorders on purpose."""

    def test_loss_duplication_and_reordering(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))

        ids = {"room_id": new_id(), "session_id": new_id(), "question_id": new_id(), "attempt_id": new_id()}
        binding = str(uuid.uuid4())
        live = udp.LiveStatusFilter(sender.getsockname())
        live.confirm_binding(binding)
        live.set_context(control_rev=1, **ids)

        def snapshot(seq):
            return udp.encode_datagram(live_status(
                binding_id=binding, control_rev=1, seq=seq, **ids,
                data={"remaining_ms": 10_000 - seq * 500, "answered_count": 0, "eligible_count": 5}))

        # seq 3 lost, seq 5 duplicated, seq 8 delivered after seq 9.
        send_order = [1, 2, 4, 5, 5, 6, 7, 9, 8, 10]
        for seq in send_order:
            sender.sendto(snapshot(seq), receiver.getsockname())

        verdicts = []
        for _ in send_order:
            payload, source = receiver.recvfrom(udp.RECV_BUFFER)
            message, verdict = live.check(payload, source)
            verdicts.append((message["seq"], verdict, message["data"]["remaining_ms"]))
        receiver.close()
        sender.close()

        accepted = [(seq, remaining) for seq, verdict, remaining in verdicts if verdict == udp.ACCEPTED]
        self.assertEqual([seq for seq, _ in accepted], [1, 2, 4, 5, 6, 7, 9, 10])
        remaining = [r for _, r in accepted]
        self.assertEqual(remaining, sorted(remaining, reverse=True))        # countdown never goes back up
        self.assertIn((5, udp.DUPLICATE, 7500), verdicts)
        self.assertIn((8, udp.OUT_OF_ORDER, 6000), verdicts)


if __name__ == "__main__":
    unittest.main()
