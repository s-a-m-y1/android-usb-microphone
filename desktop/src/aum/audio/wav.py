"""Audio helpers: level metering and WAV capture (Phase-1 diagnostics)."""

from __future__ import annotations

import math
import struct
import time
import wave
from pathlib import Path


class LevelMeter:
    """Running RMS level normalised to 0..1 (0 dBFS-ish reference ~ -10 dBFS)."""

    REFERENCE = 3000.0  # int16 RMS that maps to full-scale meter

    def __init__(self, reference: float = REFERENCE) -> None:
        self.reference = reference

    def rms(self, pcm16le: bytes) -> float:
        n = len(pcm16le) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", pcm16le[: n * 2])
        acc = sum(float(v) * v for v in samples)
        return math.sqrt(acc / n)

    def level(self, pcm16le: bytes) -> float:
        rms = self.rms(pcm16le)
        return max(0.0, min(1.0, rms / self.reference))


class WavWriter:
    """Dumps received PCM to a wav file (Phase-1 verification / diagnostics)."""

    def __init__(self, path: str | Path, rate: int = 48000, channels: int = 1) -> None:
        self.path = Path(path)
        self.rate = rate
        self.channels = channels
        self._wf = wave.open(str(self.path), "wb")
        self._wf.setnchannels(channels)
        self._wf.setsampwidth(2)
        self._wf.setframerate(rate)
        self.frames = 0
        self.started_at = time.monotonic()

    def write(self, pcm: bytes) -> None:
        self._wf.writeframes(pcm)
        self.frames += len(pcm) // 2

    def close(self) -> float:
        self._wf.close()
        return time.monotonic() - self.started_at

    @property
    def seconds(self) -> float:
        return self.frames / self.rate


def validate_format(rate: int, channels: int, bits: int) -> None:
    """Raise ValueError for unsupported audio formats (unit-tested)."""
    if rate < 8000 or rate > 192000:
        raise ValueError(f"sample rate {rate} out of supported range 8000..192000")
    if channels < 1 or channels > 2:
        raise ValueError(f"channel count {channels} not supported (1 or 2)")
    if bits != 16:
        raise ValueError(f"only 16-bit PCM supported, got {bits}")
