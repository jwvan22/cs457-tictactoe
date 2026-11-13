"""Tests for protocol/messages.py: validation order and builders."""

import json
import unittest

from protocol.framing import encode_message
from protocol.messages import (
    ENVELOPE_FIELDS, ErrorCode, MsgType, ProtocolError, decode_and_validate,
    make_connect, make_disconnect, make_error, make_game_over,
    make_game_start, make_lobby_wait, make_move, make_state_update,
    valid_coordinate,
)
from game.board import new_board

SCORES = {"Player_1": 0, "Player_2": 0, "draws": 0}


def frame(obj) -> bytes:
    """Encode a test object without the trailing newline."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def client_msg(msg_type="MOVE", player_id="Player_1", payload=None,
               timestamp=1727000005) -> dict:
    return {"msg_type": msg_type, "player_id": player_id,
            "payload": {"row": 0, "col": 2} if payload is None else payload,
            "timestamp": timestamp}


class ValidCases(unittest.TestCase):
    """One passing case per client message type, from the blueprint."""

    def test_connect(self):
        raw = (b'{"msg_type":"CONNECT","player_id":null,'
               b'"payload":{"alias":"Alice"},"timestamp":1727000000}')
        msg = decode_and_validate(raw, sender_is_client=True)
        self.assertEqual(msg["payload"]["alias"], "Alice")

    def test_move(self):
        raw = (b'{"msg_type":"MOVE","player_id":"Player_1",'
               b'"payload":{"row":0,"col":2},"timestamp":1727000005}')
        msg = decode_and_validate(raw, sender_is_client=True)
        self.assertEqual(msg["payload"], {"row": 0, "col": 2})

    def test_disconnect(self):
        raw = (b'{"msg_type":"DISCONNECT","player_id":"Player_2",'
               b'"payload":{"reason":"user quit"},"timestamp":1727000020}')
        decode_and_validate(raw, sender_is_client=True)

    def test_client_reading_server_message(self):
        raw = encode_message(make_lobby_wait())[:-1]
        msg = decode_and_validate(raw, sender_is_client=False)
        self.assertEqual(msg["msg_type"], "LOBBY_WAIT")


class ErrorCases(unittest.TestCase):
    """One failing case per code this module raises."""

    def assertCode(self, raw: bytes, code: ErrorCode):
        with self.assertRaises(ProtocolError) as ctx:
            decode_and_validate(raw, sender_is_client=True)
        self.assertIs(ctx.exception.code, code)

    def test_bad_utf8(self):
        self.assertCode(b"\xff\xfe", ErrorCode.MALFORMED_MESSAGE)

    def test_not_json(self):
        self.assertCode(b"hello", ErrorCode.MALFORMED_MESSAGE)

    def test_json_not_object(self):
        self.assertCode(b"[1,2,3]", ErrorCode.MALFORMED_MESSAGE)

    def test_missing_envelope_field(self):
        msg = client_msg()
        del msg["timestamp"]
        self.assertCode(frame(msg), ErrorCode.MALFORMED_MESSAGE)

    def test_extra_envelope_field(self):
        msg = client_msg()
        msg["extra"] = 1
        self.assertCode(frame(msg), ErrorCode.MALFORMED_MESSAGE)

    def test_bool_timestamp(self):
        self.assertCode(frame(client_msg(timestamp=True)),
                        ErrorCode.MALFORMED_MESSAGE)

    def test_payload_not_object(self):
        self.assertCode(frame(client_msg(payload=[0, 2])),
                        ErrorCode.MALFORMED_MESSAGE)

    def test_unknown_type(self):
        self.assertCode(frame(client_msg(msg_type="CHAT")),
                        ErrorCode.UNKNOWN_MSG_TYPE)

    def test_server_only_type_from_client(self):
        self.assertCode(frame(client_msg(msg_type="GAME_OVER")),
                        ErrorCode.UNKNOWN_MSG_TYPE)

    def test_alias_too_long(self):
        msg = client_msg("CONNECT", None, {"alias": "a" * 17})
        self.assertCode(frame(msg), ErrorCode.INVALID_ALIAS)

    def test_alias_bad_characters(self):
        msg = client_msg("CONNECT", None, {"alias": "Al ice!"})
        self.assertCode(frame(msg), ErrorCode.INVALID_ALIAS)

    def test_alias_missing(self):
        self.assertCode(frame(client_msg("CONNECT", None, {})),
                        ErrorCode.INVALID_ALIAS)

    def test_move_extra_payload_field(self):
        msg = client_msg(payload={"row": 0, "col": 1, "mark": "X"})
        self.assertCode(frame(msg), ErrorCode.MALFORMED_MESSAGE)

    def test_disconnect_reason_too_long(self):
        msg = client_msg("DISCONNECT", payload={"reason": "x" * 65})
        self.assertCode(frame(msg), ErrorCode.MALFORMED_MESSAGE)

    def test_envelope_checked_before_type(self):
        # Unknown type AND missing field: check 3 fails first.
        msg = client_msg(msg_type="CHAT")
        del msg["payload"]
        self.assertCode(frame(msg), ErrorCode.MALFORMED_MESSAGE)


class CoordinateTests(unittest.TestCase):

    def test_valid_values(self):
        for value in (0, 1, 2):
            self.assertTrue(valid_coordinate(value))

    def test_invalid_values(self):
        for value in (-1, 3, "1", 1.0, True, False, None):
            self.assertFalse(valid_coordinate(value), value)


class BuilderTests(unittest.TestCase):
    """Every builder emits exactly the four envelope fields."""

    def test_all_builders_have_four_fields(self):
        board = new_board()
        built = [
            make_connect("Alice"),
            make_move("Player_1", 0, 2),
            make_disconnect("Player_2", "user quit"),
            make_lobby_wait(),
            make_game_start("Player_1", "X", "Bob", "Player_1", 1, SCORES),
            make_state_update(board, "Player_1", 0, None, 1, SCORES),
            make_error(ErrorCode.OUT_OF_TURN, "It is Player_2's turn"),
            make_game_over("DRAW", None, None, board, "Board full", 1,
                           SCORES, True),
        ]
        for msg in built:
            self.assertEqual(set(msg), ENVELOPE_FIELDS)
            self.assertIn(msg["msg_type"], {t.value for t in MsgType})
            self.assertIsInstance(msg["timestamp"], int)

    def test_builders_round_trip_through_validation(self):
        for msg in (make_connect("Bob"), make_move("Player_2", 1, 1),
                    make_disconnect("Player_1", "")):
            raw = encode_message(msg)[:-1]
            self.assertEqual(decode_and_validate(raw, True), msg)

    def test_game_start_payload_matches_blueprint(self):
        msg = make_game_start("Player_1", "X", "Bob", "Player_1", 1, SCORES)
        self.assertEqual(msg["player_id"], "SERVER")
        self.assertEqual(msg["payload"], {
            "your_id": "Player_1", "your_mark": "X",
            "opponent_alias": "Bob", "first_turn": "Player_1",
            "round": 1, "scores": SCORES,
        })

    def test_error_payload(self):
        msg = make_error(ErrorCode.GAME_FULL, "Game is full", fatal=True)
        self.assertEqual(msg["payload"], {
            "code": "GAME_FULL", "detail": "Game is full", "fatal": True,
        })

    def test_builders_copy_mutable_inputs(self):
        board = new_board()
        msg = make_state_update(board, "Player_1", 0, None, 1, SCORES)
        board[0][0] = "X"
        self.assertEqual(msg["payload"]["board"][0][0], "-")


if __name__ == "__main__":
    unittest.main()
