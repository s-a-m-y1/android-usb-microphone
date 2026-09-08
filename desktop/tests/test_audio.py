"""Tests for audio helpers: level meter, format validation, WAV writing."""

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from aum.audio.wav import LevelMeter, WavWriter, validate_format


def sine_pcm(freq: float, seconds: float, rate: int = 48000,
             amplitude: int = 8000) -> bytes:
    n = int(rate * seconds)
    out = bytearray()
    for i in range(n):
        out += struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq * i / rate)))
    return bytes(out)


class TestLevelMeter(unittest.TestCase):
    def test_silence_is_zero(self):
        self.assertEqual(LevelMeter().level(b"\x00" * 4800), 0.0)

    def test_sine_level(self):
        meter = LevelMeter()
        # sine RMS = A/sqrt(2); reference is RMS-based, so a sine whose RMS
        # equals the reference must read 1.0
        lvl = meter.level(sine_pcm(440, 0.1, amplitude=int(3000 * 2 ** 0.5)))
        self.assertAlmostEqual(lvl, 1.0, delta=0.05)

    def test_quiet_signal(self):
        lvl = LevelMeter().level(sine_pcm(440, 0.1, amplitude=300))
        self.assertAlmostEqual(lvl, 0.1, delta=0.05)

    def test_rms_math(self):
        # DC value 1000 across buffer -> rms 1000
        pcm = struct.pack("<1000h", *([1000] * 1000))
        self.assertAlmostEqual(LevelMeter().rms(pcm), 1000.0, delta=0.01)

    def test_empty(self):
        self.assertEqual(LevelMeter().rms(b""), 0.0)


class TestValidateFormat(unittest.TestCase):
    def test_valid(self):
        validate_format(48000, 1, 16)          # must not raise

    def test_bad_rate(self):
        with self.assertRaises(ValueError):
            validate_format(1000, 1, 16)
        with self.assertRaises(ValueError):
            validate_format(300000, 1, 16)

    def test_bad_channels(self):
        with self.assertRaises(ValueError):
            validate_format(48000, 5, 16)

    def test_bad_bits(self):
        with self.assertRaises(ValueError):
            validate_format(48000, 1, 8)


class TestWavWriter(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.wav"
            pcm = sine_pcm(440, 0.05)
            w = WavWriter(path, rate=48000, channels=1)
            w.write(pcm)
            w.write(pcm)
            self.assertAlmostEqual(w.seconds, 0.1, delta=0.001)
            w.close()
            with wave.open(str(path)) as r:
                self.assertEqual(r.getframerate(), 48000)
                self.assertEqual(r.getnchannels(), 1)
                self.assertEqual(r.getnframes(), 4800)


if __name__ == "__main__":
    unittest.main()
