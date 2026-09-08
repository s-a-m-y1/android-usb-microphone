"""Framing protocol shared by the phone app and the PipeWire daemon.

Frame layout (little-endian):
    [u8 type][u8 flags][u16 length][payload bytes]

Types:
    0x01 PCM16LE audio payload
    0x02 JSON control line  (e.g. handshake, {"cmd":...})
    0x03 JSON event line    (daemon -> client)
    0x04 JSON event line    (phone  -> host, e.g. level updates)
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum

HEADER = struct.Struct("<BBH")
MAX_PAYLOAD = 1 << 16  # u16 length field limit


class FrameType(IntEnum):
    PCM = 0x01
    JSON = 0x02
    EVENT = 0x03
    PHONE_EVENT = 0x04


class FrameError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    type: FrameType
    payload: bytes

    @property
    def json(self) -> dict:
        try:
            return json.loads(self.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise FrameError(f"bad JSON payload: {e}") from e


def encode_frame(ftype: FrameType, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise FrameError(f"payload too large: {len(payload)} > {MAX_PAYLOAD}")
    return HEADER.pack(int(ftype), 0, len(payload)) + payload


def encode_json(ftype: FrameType, obj: dict) -> bytes:
    return encode_frame(ftype, json.dumps(obj, separators=(",", ":")).encode())


class FrameParser:
    """Incremental parser for a byte stream (unit-tested)."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        frames: list[Frame] = []
        while True:
            if len(self._buf) < HEADER.size:
                break
            ftype, _flags, length = HEADER.unpack_from(self._buf, 0)
            if ftype not in FrameType:
                raise FrameError(f"unknown frame type 0x{ftype:02x}")
            if length > MAX_PAYLOAD:
                raise FrameError(f"declared length {length} exceeds maximum")
            if len(self._buf) < HEADER.size + length:
                break
            payload = bytes(self._buf[HEADER.size:HEADER.size + length])
            del self._buf[:HEADER.size + length]
            frames.append(Frame(FrameType(ftype), payload))
        return frames


__all__ = ["Frame", "FrameType", "FrameError", "FrameParser",
           "encode_frame", "encode_json", "HEADER", "MAX_PAYLOAD"]
