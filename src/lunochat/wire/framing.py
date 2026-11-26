"""Length-prefixed framing over asyncio streams.

Frame layout:  [ 4 bytes: big-endian length N ][ N bytes: payload ]

Rules:
* N may not exceed MAX_FRAME (64 KiB). The check happens BEFORE the body is read,
  so a hostile header cannot make us allocate a huge buffer.
* An idle connection may wait forever for the *start* of a frame, but once a frame
  has started, the rest must arrive within `timeout` seconds. This stops a peer that
  sends 3 bytes and then goes silent from holding a connection open ("slow-loris").
"""

import asyncio
import struct

MAX_FRAME = 64 * 1024
DEFAULT_TIMEOUT = 30.0
_HEADER = struct.Struct(">I")


class FramingError(Exception):
    """The peer violated the framing rules (too large, stalled mid-frame)."""


class ConnectionClosed(ConnectionError):
    """The peer closed the connection."""


async def read_frame(reader: asyncio.StreamReader, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    # 1. Wait (possibly forever) for the first byte of the next frame.
    try:
        first = await reader.readexactly(1)
    except asyncio.IncompleteReadError:
        raise ConnectionClosed("peer closed the connection") from None

    # 2. From here on, the peer must keep sending.
    try:
        rest = await asyncio.wait_for(reader.readexactly(_HEADER.size - 1), timeout)
        (length,) = _HEADER.unpack(first + rest)
        if length > MAX_FRAME:
            raise FramingError(f"frame too large: {length} > {MAX_FRAME}")
        return await asyncio.wait_for(reader.readexactly(length), timeout)
    except asyncio.IncompleteReadError:
        raise ConnectionClosed("peer closed the connection mid-frame") from None
    except asyncio.TimeoutError:
        raise FramingError("peer stalled mid-frame") from None


async def write_frame(
    writer: asyncio.StreamWriter, payload: bytes, timeout: float = DEFAULT_TIMEOUT
) -> None:
    if len(payload) > MAX_FRAME:
        raise FramingError(f"frame too large: {len(payload)} > {MAX_FRAME}")
    writer.write(_HEADER.pack(len(payload)) + payload)
    try:
        await asyncio.wait_for(writer.drain(), timeout)
    except asyncio.TimeoutError:
        raise FramingError("peer is not reading") from None