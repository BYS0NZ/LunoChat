"""The message envelope: what every application message looks like inside a session.

An envelope is a CBOR map with small integer keys (see spec section 12.2):

    0 version      1 type        2 message id    3 timestamp (ms)
    4 conversation 5 seq         6 reply-to      7 critical
    8 body         15 padding

This module only turns envelopes into bytes and back. It does no networking and
holds no state, so it is easy to test.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal

import cbor2

PROTOCOL_VERSION = 1
MAX_ENVELOPE = 64 * 1024
MAX_DEPTH = 16
MAX_ITEMS = 1024
MAX_TYPE_LEN = 64
PAD_CLASSES = (256, 1024, 4 * 1024, 16 * 1024, 64 * 1024)

# Integer keys of the CBOR map
K_VERSION, K_TYPE, K_ID, K_TS = 0, 1, 2, 3
K_CONV, K_SEQ, K_REPLY_TO, K_CRITICAL, K_BODY, K_PAD = 4, 5, 6, 7, 8, 15


class ErrorCode(IntEnum):
    MALFORMED = 1
    UNSUPPORTED_VERSION = 2
    UNSUPPORTED_TYPE = 3
    TOO_LARGE = 6


class EnvelopeError(Exception):
    def __init__(self, code: ErrorCode, reason: str):
        super().__init__(f"{code.name.lower()}: {reason}")
        self.code = code
        self.reason = reason


def new_message_id(now_ms: int | None = None) -> bytes:
    """A UUIDv7: 48-bit millisecond timestamp + random bits.

    Unique without coordination, and sorts by creation time.
    """
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    raw = bytearray(now_ms.to_bytes(6, "big") + os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70  # version 7
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 4122 variant
    return bytes(raw)


@dataclass
class Envelope:
    type: str
    body: dict[Any, Any] = field(default_factory=dict)
    msg_id: bytes = field(default_factory=new_message_id)
    timestamp_ms: int = field(default_factory=lambda: time.time_ns() // 1_000_000)
    version: int = PROTOCOL_VERSION
    conversation_id: bytes | None = None
    seq: int | None = None
    reply_to: bytes | None = None
    critical: bool = False


# ---------------------------------------------------------------- encoding


def _to_map(env: Envelope, padding: bytes | None) -> dict[int, Any]:
    m: dict[int, Any] = {
        K_VERSION: env.version,
        K_TYPE: env.type,
        K_ID: env.msg_id,
        K_TS: env.timestamp_ms,
    }
    if env.conversation_id is not None:
        m[K_CONV] = env.conversation_id
    if env.seq is not None:
        m[K_SEQ] = env.seq
    if env.reply_to is not None:
        m[K_REPLY_TO] = env.reply_to
    if env.critical:
        m[K_CRITICAL] = True
    m[K_BODY] = env.body
    if padding is not None:
        m[K_PAD] = padding
    return m


def encode(env: Envelope, pad: bool = True) -> bytes:
    """Encode to canonical CBOR, padded up to the next size class."""
    base = cbor2.dumps(_to_map(env, None), canonical=True)
    if len(base) > MAX_ENVELOPE:
        raise EnvelopeError(ErrorCode.TOO_LARGE, f"envelope is {len(base)} bytes")
    if not pad:
        return base

    for target in PAD_CLASSES:
        if len(base) < target:
            # The padding entry costs 1 (key) + 1..3 (bytes header) + n bytes.
            for overhead in (2, 3, 4):
                n = target - len(base) - overhead
                if n >= 0:
                    out = cbor2.dumps(_to_map(env, bytes(n)), canonical=True)
                    if len(out) == target:
                        return out
    return base  # already at (or just under) the largest class


# ---------------------------------------------------------------- decoding

_ALLOWED_SCALARS = (int, float, str, bytes, bool, type(None))


def _check_value(value: Any, depth: int) -> None:
    """Reject anything outside plain CBOR data: tags, huge or deep structures."""
    if depth > MAX_DEPTH:
        raise EnvelopeError(ErrorCode.MALFORMED, "nesting too deep")
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise EnvelopeError(ErrorCode.MALFORMED, "too many map entries")
        for k, v in value.items():
            if not isinstance(k, (int, str)) or isinstance(k, bool):
                raise EnvelopeError(ErrorCode.MALFORMED, "bad map key")
            _check_value(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise EnvelopeError(ErrorCode.MALFORMED, "too many array items")
        for v in value:
            _check_value(v, depth + 1)
    elif not isinstance(value, _ALLOWED_SCALARS):
        # CBOR tags decode into richer Python types (datetime, CBORTag, ...): not allowed.
        raise EnvelopeError(ErrorCode.MALFORMED, f"disallowed type {type(value).__name__}")


def _field(m: dict, key: int, kind: type, *, size: int | None = None, required: bool = False):
    if key not in m:
        if required:
            raise EnvelopeError(ErrorCode.MALFORMED, f"missing field {key}")
        return None
    v = m[key]
    if not isinstance(v, kind) or (kind is int and isinstance(v, bool)):
        raise EnvelopeError(ErrorCode.MALFORMED, f"field {key} has the wrong type")
    if size is not None and len(v) != size:
        raise EnvelopeError(ErrorCode.MALFORMED, f"field {key} must be {size} bytes")
    return v


def decode(data: bytes) -> Envelope:
    """Parse and validate one envelope. Raises EnvelopeError, never anything else."""
    if len(data) > MAX_ENVELOPE:
        raise EnvelopeError(ErrorCode.TOO_LARGE, "envelope too large")
    try:
        m = cbor2.loads(data)
    except (cbor2.CBORDecodeError, RecursionError, ValueError, TypeError, EOFError,
            MemoryError, OverflowError, UnicodeDecodeError) as e:
        raise EnvelopeError(ErrorCode.MALFORMED, f"not valid CBOR ({type(e).__name__})") from None

    if not isinstance(m, dict):
        raise EnvelopeError(ErrorCode.MALFORMED, "envelope is not a map")
    _check_value(m, 0)

    version = _field(m, K_VERSION, int, required=True)
    if version != PROTOCOL_VERSION:
        raise EnvelopeError(ErrorCode.UNSUPPORTED_VERSION, f"version {version}")

    type_ = _field(m, K_TYPE, str, required=True)
    if not 0 < len(type_) <= MAX_TYPE_LEN:
        raise EnvelopeError(ErrorCode.MALFORMED, "bad type length")
    msg_id = _field(m, K_ID, bytes, size=16, required=True)
    ts = _field(m, K_TS, int, required=True)
    if ts < 0:
        raise EnvelopeError(ErrorCode.MALFORMED, "negative timestamp")
    seq = _field(m, K_SEQ, int)
    if seq is not None and seq < 0:
        raise EnvelopeError(ErrorCode.MALFORMED, "negative seq")
    body = m.get(K_BODY, {})
    if not isinstance(body, dict):
        raise EnvelopeError(ErrorCode.MALFORMED, "body must be a map")

    # Unknown keys (and the padding, key 15) are ignored on purpose.
    return Envelope(
        type=type_,
        body=body,
        msg_id=msg_id,
        timestamp_ms=ts,
        version=version,
        conversation_id=_field(m, K_CONV, bytes, size=16),
        seq=seq,
        reply_to=_field(m, K_REPLY_TO, bytes, size=16),
        critical=bool(_field(m, K_CRITICAL, bool)),
    )


def handling(env: Envelope, known_types: set[str]) -> Literal["handle", "ignore", "unsupported"]:
    """What to do with an envelope (spec 12.2): unknown + non-critical is ignored,
    unknown + critical is answered with an `unsupported_type` error."""
    if env.type in known_types:
        return "handle"
    return "unsupported" if env.critical else "ignore"