import socket
import struct

MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MB, reject anything bigger


def send_message(sock: socket.socket, data: bytes) -> None:
    """Send one message, prefixed with its length."""
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("message too large")
    header = struct.pack(">I", len(data))   # ">I" = 4-byte unsigned int, big-endian
    sock.sendall(header + data)


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes, or raise ConnectionError if the peer closes early."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_message(sock: socket.socket) -> bytes:
    """Receive one complete message."""
    header = _recv_exactly(sock, 4)
    (length,) = struct.unpack(">I", header)
    if length > MAX_MESSAGE_SIZE:
        raise ValueError("message too large")
    return _recv_exactly(sock, length)