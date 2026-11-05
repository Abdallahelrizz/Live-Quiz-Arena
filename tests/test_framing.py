"""Tests for TCP framing (report section 2.2: message boundaries over TCP).

Most tests use real TCP connections on 127.0.0.1, so the bytes really pass
through the operating system's TCP stack. The sender splits or glues frames
on purpose and the receiver must still recover each message exactly once.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from fatrat.protocol.framing import (
    MAX_FRAME, ConnectionClosed, FrameLengthError, FrameTimeoutError, PayloadError,
    decode_json, encode_frame, recv_frame, recv_message,
)
from tests.samples import client_message


def tcp_pair():
    """Return (client, server) ends of a real TCP connection on 127.0.0.1."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname())
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # send small writes immediately
    server, _ = listener.accept()
    listener.close()
    return client, server


class FramingTest(unittest.TestCase):
    def setUp(self):
        self.client, self.server = tcp_pair()
        self.server.settimeout(5)            # idle timeout so a broken test cannot hang

    def tearDown(self):
        self.client.close()
        self.server.close()

    # -- frame format ------------------------------------------------------

    def test_header_is_big_endian_byte_length_of_utf8_body(self):
        message = client_message("JOIN_ROOM")
        message["data"]["username"] = "فأر 🧀"            # non-ASCII: length counts bytes, not characters
        frame = encode_frame(message)
        (length,) = struct.unpack("!I", frame[:4])
        self.assertEqual(length, len(frame) - 4)
        self.assertIn("فأر 🧀".encode("utf-8"), frame)   # sent as raw UTF-8, not \\u escapes
        self.assertNotIn(b": ", frame)                      # compact separators

    def test_one_message_round_trip(self):
        message = client_message("SUBMIT_ANSWER")
        self.client.sendall(encode_frame(message))
        self.assertEqual(recv_message(self.server), message)

    # -- partial reads -------------------------------------------------------

    def test_message_sent_one_byte_at_a_time(self):
        message = client_message("JOIN_ROOM")
        frame = encode_frame(message)

        def trickle():
            for i in range(len(frame)):
                self.client.sendall(frame[i:i + 1])
                time.sleep(0.001)

        sender = threading.Thread(target=trickle)
        sender.start()
        self.assertEqual(recv_message(self.server), message)
        sender.join()

    def test_split_at_every_position_including_inside_header(self):
        message = client_message("GET_STATE")
        frame = encode_frame(message)
        for cut in range(1, len(frame)):
            with self.subTest(cut=cut):
                self.client.sendall(frame[:cut])
                timer = threading.Timer(0.002, self.client.sendall, args=(frame[cut:],))
                timer.start()
                self.assertEqual(recv_message(self.server), message)
                timer.join()

    # -- several messages in one read --------------------------------------

    def test_three_messages_in_one_send(self):
        messages = [client_message("GET_STATE"), client_message("SUBMIT_ANSWER"), client_message("LEAVE_ROOM")]
        self.client.sendall(b"".join(encode_frame(m) for m in messages))
        self.assertEqual([recv_message(self.server) for _ in messages], messages)

    def test_one_and_a_half_messages_then_the_rest(self):
        first, second = client_message("GET_STATE"), client_message("REQUEST_UDP_BIND")
        stream = encode_frame(first) + encode_frame(second)
        half = len(encode_frame(first)) + 3            # first frame + 3 bytes of the second header
        self.client.sendall(stream[:half])
        self.assertEqual(recv_message(self.server), first)
        self.client.sendall(stream[half:])
        self.assertEqual(recv_message(self.server), second)

    # -- length limits -----------------------------------------------------

    def test_length_zero_is_rejected(self):
        self.client.sendall(struct.pack("!I", 0))
        with self.assertRaises(FrameLengthError):
            recv_frame(self.server)

    def test_length_above_1_mib_is_rejected_without_reading_the_body(self):
        self.client.sendall(struct.pack("!I", MAX_FRAME + 1))
        started = time.monotonic()
        with self.assertRaises(FrameLengthError):
            recv_frame(self.server)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_largest_allowed_frame_is_accepted(self):
        body = b"{" + b'"p":"' + b"x" * (MAX_FRAME - 8) + b'"}'
        self.assertEqual(len(body), MAX_FRAME)
        sender = threading.Thread(target=self.client.sendall, args=(struct.pack("!I", MAX_FRAME) + body,))
        sender.start()
        self.assertEqual(len(recv_frame(self.server)), MAX_FRAME)
        sender.join()

    def test_encoder_refuses_frames_over_1_mib(self):
        with self.assertRaises(ValueError):
            encode_frame({"p": "x" * MAX_FRAME})

    # -- end of stream and stalls ----------------------------------------

    def test_clean_eof_between_frames(self):
        self.client.close()
        with self.assertRaises(ConnectionClosed) as caught:
            recv_frame(self.server)
        self.assertFalse(caught.exception.mid_frame)

    def test_eof_inside_header_and_inside_body(self):
        for partial in (b"\x00\x00", struct.pack("!I", 50) + b'{"v":1'):
            with self.subTest(partial=partial):
                client, server = tcp_pair()
                server.settimeout(5)
                client.sendall(partial)
                client.close()
                with self.assertRaises(ConnectionClosed) as caught:
                    recv_frame(server)
                self.assertTrue(caught.exception.mid_frame)
                server.close()

    def test_stalled_partial_frame_times_out(self):
        self.client.sendall(struct.pack("!I", 100) + b'{"v":1')
        started = time.monotonic()
        with self.assertRaises(FrameTimeoutError):
            recv_frame(self.server, partial_timeout=0.3)
        self.assertGreaterEqual(time.monotonic() - started, 0.29)

    def test_idle_connection_is_not_a_partial_frame(self):
        self.server.settimeout(0.3)         # caller's idle timeout
        with self.assertRaises(socket.timeout):
            recv_frame(self.server, partial_timeout=0.1)


class StrictJsonTest(unittest.TestCase):
    """Fatal payload errors from report section 2.2."""

    def test_rejected_payloads(self):
        bad = {
            "invalid UTF-8": b'{"v":1,"type":"\xff"}',
            "invalid JSON": b'{"v":1,"type":',
            "duplicate key": b'{"v":1,"v":1,"type":"GET_STATE","data":{}}',
            "NaN": b'{"v":1,"x":NaN}',
            "Infinity": b'{"v":1,"x":-Infinity}',
            "overflow to infinity": b'{"v":1,"x":1e999}',
            "array": b'[{"v":1}]',
            "string": b'"hello"',
        }
        for name, payload in bad.items():
            with self.subTest(name):
                with self.assertRaises(PayloadError):
                    decode_json(payload)

    def test_accepted_payload(self):
        self.assertEqual(decode_json('{"v":1,"name":"جبنة"}'.encode()), {"v": 1, "name": "جبنة"})


if __name__ == "__main__":
    unittest.main()
