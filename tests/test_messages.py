"""Tests for message formats and invalid-message handling (report sections 2.2-2.3)."""

from __future__ import annotations

import copy
import unittest

from fatrat.protocol.catalogue import CATALOGUE, ERROR_CODES, MESSAGES, TCP, UDP
from fatrat.protocol.messages import (
    MAX_INT, ProtocolError, is_token, validate_message, validate_question, validate_quiz,
    validate_result, validate_state,
)
from tests.samples import client_message, live_status, new_id, new_token, player_state, question

CLIENT_TYPES = [m.name for m in MESSAGES if m.from_client]


def check_client(message, transport=None):
    spec = CATALOGUE.get(message.get("type"))
    transport = transport or (spec.transport if spec else TCP)
    return validate_message(message, transport=transport, from_server=False)


class CatalogueTest(unittest.TestCase):
    def test_32_unique_message_types(self):
        self.assertEqual(len(MESSAGES), 32)
        self.assertEqual(len(CATALOGUE), 32)

    def test_directions_and_transports(self):
        self.assertEqual(len(CLIENT_TYPES), 24)
        self.assertEqual({m.name for m in MESSAGES if m.transport == UDP}, {"UDP_HELLO", "LIVE_STATUS"})
        # Everything that changes a room or affects scoring is on TCP.
        for name in ("SUBMIT_ANSWER", "START_QUIZ", "JOIN_ROOM", "RESULT", "STATE", "ERROR"):
            self.assertEqual(CATALOGUE[name].transport, TCP, name)

    def test_32_error_codes(self):
        self.assertEqual(len(ERROR_CODES), 32)


class ValidClientMessagesTest(unittest.TestCase):
    def test_every_client_message_type_validates(self):
        for mtype in CLIENT_TYPES:
            with self.subTest(mtype):
                self.assertEqual(check_client(client_message(mtype)).name, mtype)

    def test_report_example_submit_answer(self):
        # Section 2.8 example, with real UUIDs in place of the placeholders.
        message = {"v": 1, "type": "SUBMIT_ANSWER", "request_id": new_id(), "room_id": new_id(),
                   "data": {"question_id": new_id(), "attempt_id": new_id(), "choice_id": new_id()}}
        check_client(message)


class InvalidClientMessagesTest(unittest.TestCase):
    def assert_rejected(self, message, code="INVALID_MESSAGE", fatal=False, transport=None):
        with self.assertRaises(ProtocolError) as caught:
            check_client(message, transport)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.fatal, fatal)
        return caught.exception

    def test_each_missing_data_field_is_rejected(self):
        for mtype in CLIENT_TYPES:
            for field in CATALOGUE[mtype].data:
                with self.subTest(type=mtype, field=field):
                    message = client_message(mtype)
                    del message["data"][field]
                    self.assert_rejected(message)

    def test_each_wrongly_typed_data_field_is_rejected(self):
        for mtype in CLIENT_TYPES:
            for field in CATALOGUE[mtype].data:
                with self.subTest(type=mtype, field=field):
                    message = client_message(mtype)
                    message["data"][field] = {"wrong": "type"}
                    self.assert_rejected(message)

    def test_unknown_fields_are_rejected(self):
        message = client_message("GET_STATE")
        message["data"]["colour"] = "yellow"
        self.assert_rejected(message)
        message = client_message("GET_STATE")
        message["priority"] = 1
        self.assert_rejected(message)

    def test_error_is_correlated_with_a_usable_request_id(self):
        message = client_message("SUBMIT_ANSWER")
        del message["data"]["choice_id"]
        self.assertEqual(self.assert_rejected(message).reply_to, message["request_id"])
        message["request_id"] = "not-a-uuid"
        self.assertIsNone(self.assert_rejected(message).reply_to)

    def test_versions(self):
        message = client_message("GET_STATE")
        message["v"] = 2
        error = self.assert_rejected(message, code="UNSUPPORTED_VERSION", fatal=True)
        self.assertEqual(error.reply_to, message["request_id"])
        for bad_v in (True, "1", 1.0, None):
            with self.subTest(v=bad_v):
                message["v"] = bad_v
                self.assert_rejected(message)
        del message["v"]
        self.assert_rejected(message)

    def test_unknown_type_and_missing_data(self):
        message = client_message("GET_STATE")
        message["type"] = "DANCE"
        self.assert_rejected(message)
        message = client_message("GET_STATE")
        message["data"] = []
        self.assert_rejected(message)

    def test_room_id_required_only_after_attachment(self):
        message = client_message("SUBMIT_ANSWER")
        del message["room_id"]
        self.assert_rejected(message)
        check_client(client_message("JOIN_ROOM"))         # unattached: no room_id needed
        message = client_message("JOIN_ROOM")
        message["room_id"] = new_id()                       # and not allowed
        self.assert_rejected(message)

    def test_wrong_transport_or_direction(self):
        self.assert_rejected(client_message("UDP_HELLO"), transport=TCP)
        self.assert_rejected(client_message("GET_STATE"), transport=UDP)
        state = player_state()
        state["request_id"] = new_id()
        self.assert_rejected(state)                         # a client may not send STATE

    def test_integer_rules(self):
        message = client_message("START_QUIZ")
        for bad in (-1, MAX_INT + 1, True, 1.0, "3"):
            with self.subTest(value=bad):
                message["data"]["expected_control_rev"] = bad
                self.assert_rejected(message)
        message["data"]["expected_control_rev"] = MAX_INT
        check_client(message)

    def test_identifier_formats(self):
        message = client_message("SUBMIT_ANSWER")
        for bad in ("question-uuid", new_id().upper(), "{" + new_id() + "}",
                    "00000000-0000-1000-8000-000000000000"):   # version 1, not 4
            with self.subTest(value=bad):
                message["data"]["question_id"] = bad
                self.assert_rejected(message)

    def test_host_token_format(self):
        token = new_token()
        self.assertTrue(is_token(token))
        bad = [token[:-1], token + "A", token[:-1] + "=", token[:-1] + "+", token[:-1] + "/"]
        for value in bad:
            with self.subTest(value=value):
                self.assertFalse(is_token(value))
        message = client_message("CREATE_ROOM")
        message["data"]["host_token"] = token[:42]
        self.assert_rejected(message)

    def test_non_canonical_token_is_rejected(self):
        token = new_token()
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        last = alphabet.index(token[-1])
        # 32 bytes = 256 bits but 43 characters hold 258, so the last character
        # has 2 unused low bits that must be zero. Setting one is non-canonical.
        changed = token[:-1] + alphabet[last | 1]
        self.assertFalse(is_token(changed))

    def test_udp_hello_nonce(self):
        message = client_message("UDP_HELLO")
        message["data"]["nonce"] = message["data"]["nonce"].upper()
        self.assert_rejected(message)


class QuizRulesTest(unittest.TestCase):
    def assert_invalid_quiz(self, fn, *args):
        with self.assertRaises(ProtocolError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, "INVALID_QUIZ")

    def test_choice_count_boundaries(self):
        validate_question(question(choices=2))
        validate_question(question(choices=8))
        self.assert_invalid_quiz(validate_question, question(choices=1))
        self.assert_invalid_quiz(validate_question, question(choices=9))

    def test_question_count_boundaries(self):
        validate_quiz("Quiz", [question()])
        validate_quiz("Quiz", [question() for _ in range(30)])
        self.assert_invalid_quiz(validate_quiz, "Quiz", [])
        self.assert_invalid_quiz(validate_quiz, "Quiz", [question() for _ in range(31)])

    def test_timer_boundaries(self):
        validate_question(question(time_limit_ms=1_000))
        validate_question(question(time_limit_ms=300_000))
        self.assert_invalid_quiz(validate_question, question(time_limit_ms=999))
        self.assert_invalid_quiz(validate_question, question(time_limit_ms=300_001))

    def test_text_limits_count_characters_not_bytes(self):
        validate_question(question(text="🧀" * 1000))      # 1000 characters, 4000 bytes
        self.assert_invalid_quiz(validate_question, question(text="🧀" * 1001))
        self.assert_invalid_quiz(validate_question, question(text="   "))
        validate_quiz("t" * 100, [question()])
        self.assert_invalid_quiz(validate_quiz, "t" * 101, [question()])

    def test_choice_identity_rules(self):
        q = question()
        q["correct_choice_id"] = new_id()
        self.assert_invalid_quiz(validate_question, q)
        q = question()
        q["choices"][1]["choice_id"] = q["choices"][0]["choice_id"]
        self.assert_invalid_quiz(validate_question, q)
        q1 = question()
        q2 = copy.deepcopy(q1)
        self.assert_invalid_quiz(validate_quiz, "Quiz", [q1, q2])  # duplicate question IDs


class ServerMessagesTest(unittest.TestCase):
    def check_server(self, message, transport=TCP, strict=True):
        return validate_message(message, transport=transport, from_server=True, strict=strict)

    def error(self, code, **extra):
        return {"v": 1, "type": "ERROR", "reply_to": new_id(),
                "data": {"code": code, "message": "text", "retryable": False, **extra}}

    def test_report_example_result_and_live_status(self):
        self.check_server({"v": 1, "type": "RESULT", "reply_to": new_id(), "room_id": new_id(),
                           "data": {"accepted": True, "question_id": new_id(), "attempt_id": new_id(),
                                    "choice_id": new_id()}})
        self.check_server(live_status(control_rev=12, seq=8), transport=UDP)

    def test_error_rules(self):
        self.check_server(self.error("INVALID_PIN"))
        self.check_server(self.error("STALE_STATE", current_control_rev=4))
        bad = [
            self.error("STALE_STATE"),                                   # missing current_control_rev
            self.error("SESSION_REVOKED"),                               # missing reason
            self.error("SESSION_REVOKED", reason="BORED"),
            self.error("ALREADY_ANSWERED", question_id=new_id()),        # missing attempt/choice IDs
            self.error("HOST_HAS_ROOM"),                                 # missing existing_room_id
            self.error("NO_SUCH_CODE"),
            self.error("INVALID_PIN", reason="HOST_REMOVED"),            # reason only for SESSION_REVOKED
        ]
        retry = self.error("INVALID_PIN")
        retry["data"]["retryable"] = True                                # only RATE_LIMITED/SERVER_BUSY
        bad.append(retry)
        for message in bad:
            with self.subTest(data=message["data"]):
                with self.assertRaises(ProtocolError):
                    self.check_server(message)
        busy = self.error("SERVER_BUSY", retry_after_ms=500)
        busy["data"]["retryable"] = True
        self.check_server(busy)

    def test_clients_ignore_server_extension_fields_unless_strict(self):
        message = self.error("INVALID_PIN")
        message["data"]["hint"] = "extension"
        self.check_server(message, strict=False)
        with self.assertRaises(ProtocolError):
            self.check_server(message, strict=True)

    def test_result_data_per_request(self):
        identity = {"room_id": new_id(), "pin": "000123", "session_id": new_id(), "actor_id": new_id(),
                    "role": "PLAYER", "reconnect_token": new_token()}
        validate_result(identity, "JOIN_ROOM")
        for field in identity:
            broken = dict(identity)
            del broken[field]
            with self.subTest(missing=field), self.assertRaises(ProtocolError):
                validate_result(broken, "JOIN_ROOM")
        accepted = {"accepted": True, "question_id": new_id(), "attempt_id": new_id(), "choice_id": new_id()}
        validate_result(accepted, "SUBMIT_ANSWER")
        with self.assertRaises(ProtocolError):                           # no correctness before close
            validate_result({**accepted, "correct": True}, "SUBMIT_ANSWER")
        validate_result({}, "GET_STATE")

    def test_valid_player_states(self):
        for phase in ("LOBBY", "OPEN", "RESULTS"):
            with self.subTest(phase):
                state = player_state(phase)
                self.check_server(state)
                validate_state(state, role="PLAYER")

    def test_state_invariants(self):
        def broken(change):
            state = player_state("OPEN")
            change(state["data"])
            return state

        cases = {
            "pause_reason while OPEN": lambda d: d.update(pause_reason="MANUAL"),
            "editing while OPEN": lambda d: d.update(editing=True),
            "FINISHED without finish_reason": lambda d: d.update(phase="FINISHED", remaining_ms=0),
            "remaining time with no live question": lambda d: d.update(phase="RESULTS"),
            "question text leaked to player": lambda d: d["current_question"].update(text="Which layer?"),
            "correct answer leaked to player": lambda d: d["current_question"].update(correct_choice_id=new_id()),
            "host quiz sent to player": lambda d: d.update(quiz=None),
            "symbol out of position": lambda d: d["current_question"]["choices"][0].update(symbol_index=5),
            "answered above eligible": lambda d: d.update(live={"answered_count": 6, "eligible_count": 5}),
            "rank gap": lambda d: d["leaderboard"][0].update(rank=2),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ProtocolError):
                validate_state(broken(change), role="PLAYER")


if __name__ == "__main__":
    unittest.main()
