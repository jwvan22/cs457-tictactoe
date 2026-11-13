"""Tests for protocol/framing.py using blueprint section 1.4 wire data."""

import json
import unittest

from protocol.framing import (
    MAX_FRAME_BYTES, FrameReader, FrameTooLargeError, encode_message,
)

CONNECT_FRAME = (
    b'{"msg_type":"CONNECT","player_id":null,"payload":{"alias":"Alice"},'
    b'"timestamp":1727000000}\n'
)
LOBBY_FRAME = (
    b'{"msg_type":"LOBBY_WAIT","player_id":"SERVER","payload":'
    b'{"players_connected":1,"message":"Waiting for an opponent"},'
    b'"timestamp":1727000000}\n'
)
MOVE_PART_1 = b'{"msg_type":"MOVE","player_id":"Pla'
MOVE_PART_2 = (
    b'yer_1","payload":{"row":0,"col":2},"timestamp":1727000005}\n'
)


class EncodeTests(unittest.TestCase):

    def test_matches_blueprint_wire_bytes(self):
        msg = {"msg_type": "CONNECT", "player_id": None,
               "payload": {"alias": "Alice"}, "timestamp": 1727000000}
        self.assertEqual(encode_message(msg), CONNECT_FRAME)

    def test_newline_inside_string_is_escaped(self):
        frame = encode_message({"detail": "line1\nline2"})
        self.assertEqual(frame.count(b"\n"), 1)
        self.assertTrue(frame.endswith(b"\n"))


class FrameReaderTests(unittest.TestCase):

    def test_two_frames_in_one_chunk(self):
        reader = FrameReader()
        frames = reader.feed(CONNECT_FRAME + LOBBY_FRAME)
        self.assertEqual(len(frames), 2)
        self.assertEqual(json.loads(frames[0])["msg_type"], "CONNECT")
        self.assertEqual(json.loads(frames[1])["msg_type"], "LOBBY_WAIT")
        self.assertEqual(reader.pending, 0)

    def test_frame_split_across_two_chunks(self):
        reader = FrameReader()
        self.assertEqual(reader.feed(MOVE_PART_1), [])
        frames = reader.feed(MOVE_PART_2)
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0])["payload"],
                         {"row": 0, "col": 2})

    def test_frame_split_across_three_chunks(self):
        reader = FrameReader()
        whole = CONNECT_FRAME
        self.assertEqual(reader.feed(whole[:10]), [])
        self.assertEqual(reader.feed(whole[10:40]), [])
        self.assertEqual(reader.feed(whole[40:]), [whole[:-1]])

    def test_chunk_ends_exactly_on_newline(self):
        reader = FrameReader()
        self.assertEqual(reader.feed(CONNECT_FRAME), [CONNECT_FRAME[:-1]])
        self.assertEqual(reader.pending, 0)

    def test_full_frame_then_partial_keeps_remainder(self):
        reader = FrameReader()
        frames = reader.feed(CONNECT_FRAME + MOVE_PART_1)
        self.assertEqual(len(frames), 1)
        self.assertEqual(reader.pending, len(MOVE_PART_1))
        self.assertEqual(len(reader.feed(MOVE_PART_2)), 1)

    def test_empty_chunk_returns_nothing(self):
        reader = FrameReader()
        self.assertEqual(reader.feed(b""), [])

    def test_overflow_without_newline_raises(self):
        reader = FrameReader()
        with self.assertRaises(FrameTooLargeError):
            reader.feed(b"a" * (MAX_FRAME_BYTES + 1))

    def test_exactly_at_limit_is_allowed(self):
        reader = FrameReader()
        self.assertEqual(reader.feed(b"a" * MAX_FRAME_BYTES), [])


if __name__ == "__main__":
    unittest.main()
