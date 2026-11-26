import asyncio
import struct

import pytest

from lunochat.wire.framing import (
    MAX_FRAME,
    ConnectionClosed,
    FramingError,
    read_frame,
    write_frame,
)


def run(coro):
    return asyncio.run(coro)


def make_reader(data: bytes, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


def frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def test_two_frames_back_to_back():
    async def go():
        reader = make_reader(frame(b"hello") + frame(b"world"))
        assert await read_frame(reader) == b"hello"
        assert await read_frame(reader) == b"world"

    run(go())


def test_empty_frame():
    async def go():
        assert await read_frame(make_reader(frame(b""))) == b""

    run(go())


def test_frame_arriving_one_byte_at_a_time():
    async def go():
        reader = asyncio.StreamReader()
        data = frame(b"partial reads are normal")

        async def trickle():
            for i in range(len(data)):
                reader.feed_data(data[i : i + 1])
                await asyncio.sleep(0)

        task = asyncio.create_task(trickle())
        assert await read_frame(reader) == b"partial reads are normal"
        await task

    run(go())


def test_oversized_header_is_rejected_before_reading_body():
    async def go():
        reader = make_reader(struct.pack(">I", MAX_FRAME + 1))  # no body follows
        with pytest.raises(FramingError):
            await read_frame(reader)

    run(go())


def test_clean_close_between_frames():
    async def go():
        with pytest.raises(ConnectionClosed):
            await read_frame(make_reader(b""))

    run(go())


def test_close_in_the_middle_of_a_frame():
    async def go():
        with pytest.raises(ConnectionClosed):
            await read_frame(make_reader(frame(b"hello")[:-2]))

    run(go())


def test_stalled_peer_hits_the_deadline():
    async def go():
        reader = make_reader(b"\x00\x00", eof=False)  # 2 of 4 header bytes, then silence
        with pytest.raises(FramingError):
            await read_frame(reader, timeout=0.1)

    run(go())


def test_idle_connection_may_wait_for_a_new_frame():
    async def go():
        reader = asyncio.StreamReader()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(read_frame(reader, timeout=0.05), 0.2)

    run(go())


def test_roundtrip_over_a_real_socket():
    async def go():
        received = []

        async def handler(reader, writer):
            received.append(await read_frame(reader))
            await write_frame(writer, b"pong")
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await write_frame(writer, b"ping")
            assert await read_frame(reader) == b"pong"
            writer.close()
        assert received == [b"ping"]

    run(go())


def test_write_rejects_oversized_payload():
    async def go():
        with pytest.raises(FramingError):
            await write_frame(None, b"x" * (MAX_FRAME + 1))  # type: ignore[arg-type]

    run(go())