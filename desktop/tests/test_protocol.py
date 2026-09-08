"""Tests for the framing protocol (parsing, validation, round-trips)."""

import json
import struct
import unittest

from aum.transport.protocol import (
    HEADER, Frame, FrameError, FrameParser, FrameType, encode_frame,
    encode_json,
)


def pcm_frame(payload: bytes) -> bytes:
    return encode_frame(FrameType.PCM, payload)


class TestEncode(unittest.TestCase):
    def test_roundtrip(self):
        payload = b"\x01\x02\x03"
        raw = pcm_frame(payload)
        self.assertEqual(raw[:2], b"\x01\x00")
        self.assertEqual(raw[2:4], struct.pack("<H", 3))
        frames = FrameParser().feed(raw)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].type, FrameType.PCM)
        self.assertEqual(frames[0].payload, payload)

    def test_json_encode(self):
        raw = encode_json(FrameType.JSON, {"event": "hello", "rate": 48000})
        frames = FrameParser().feed(raw)
        self.assertEqual(frames[0].json["rate"], 48000)

    def test_oversized_rejected(self):
        with self.assertRaises(FrameError):
            encode_frame(FrameType.PCM, b"x" * (65536 + 1))


class TestFrameParser(unittest.TestCase):
    def test_multiple_frames_single_feed(self):
        data = pcm_frame(b"a") + pcm_frame(b"bb") + \
            encode_json(FrameType.EVENT, {"event": "stats"})
        frames = FrameParser().feed(data)
        self.assertEqual([f.type for f in frames],
                         [FrameType.PCM, FrameType.PCM, FrameType.EVENT])
        self.assertEqual(frames[1].payload, b"bb")
        self.assertEqual(frames[2].json["event"], "stats")

    def test_incremental_feed(self):
        parser = FrameParser()
        raw = pcm_frame(b"12345")
        self.assertEqual(parser.feed(raw[:2]), [])          # partial header
        self.assertEqual(parser.feed(raw[2:4]), [])         # partial length
        frames = parser.feed(raw[4:])                       # payload complete
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].payload, b"12345")

    def test_stream_with_extra_bytes(self):
        parser = FrameParser()
        raw = pcm_frame(b"ab") + b"\x00"   # stray trailing byte (adb quirk)
        frames = parser.feed(raw)
        self.assertEqual(frames[0].payload, b"ab")
        self.assertEqual(parser.feed(b""), [])              # keeps stray byte

    def test_bad_type_raises(self):
        bad = bytes([0x7F, 0, 1, 0]) + b"Z"
        with self.assertRaises(FrameError):
            FrameParser().feed(bad)

    def test_truncated_frame_waits(self):
        # a declared length larger than the available payload must not yield
        # a frame, and must not raise
        parser = FrameParser()
        self.assertEqual(parser.feed(struct.pack("<BBH", 0x01, 0, 0xFFFF)), [])
        frames = parser.feed(b"x" * 10)
        self.assertEqual(frames, [])

    def test_json_error_on_garbage(self):
        frames = FrameParser().feed(encode_frame(FrameType.JSON, b"not json"))
        with self.assertRaises(FrameError):
            frames[0].json

    def test_header_layout(self):
        self.assertEqual(HEADER.format, "<BBH")
        self.assertEqual(HEADER.size, 4)


if __name__ == "__main__":
    unittest.main()
