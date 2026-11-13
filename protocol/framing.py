"""Newline-delimited JSON framing (protocol_blueprint.md section 1.3).

This module turns message dictionaries into wire frames and turns a raw
TCP byte stream back into complete frames. It never touches sockets, so
the same code serves the non-blocking server and the client.
"""

import json

MAX_FRAME_BYTES = 4096
FRAME_TERMINATOR = b"\n"


class FrameTooLargeError(Exception):
    """Raised when the buffer exceeds the limit with no frame terminator."""


def encode_message(msg: dict) -> bytes:
    """Serialize one message as compact JSON followed by a single newline.

    json.dumps always escapes control characters inside strings, so the
    only raw 0x0A byte in the output is the terminator appended here.
    """
    text = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8") + FRAME_TERMINATOR


class FrameReader:
    """Reassemble complete frames from arbitrary recv() chunks.

    TCP may coalesce several frames into one chunk or split one frame
    across several chunks. Each connection owns one FrameReader, which
    keeps any partial frame until the rest of it arrives.
    """

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add a chunk and return every complete frame, newline removed.

        Raises FrameTooLargeError if the bytes left over after extraction
        exceed max_frame_bytes, which means no terminator is coming.
        """
        self._buffer.extend(chunk)
        frames = []
        while True:
            index = self._buffer.find(FRAME_TERMINATOR)
            if index == -1:
                break
            frames.append(bytes(self._buffer[:index]))
            del self._buffer[:index + 1]
        if len(self._buffer) > self.max_frame_bytes:
            raise FrameTooLargeError(
                f"{len(self._buffer)} bytes buffered with no newline"
            )
        return frames

    @property
    def pending(self) -> int:
        """Number of bytes held that are not yet part of a full frame."""
        return len(self._buffer)
