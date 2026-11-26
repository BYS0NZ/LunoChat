import os
import random
from datetime import datetime, timezone

import cbor2
import pytest

from lunochat.wire.envelope import (
    MAX_ENVELOPE,
    PAD_CLASSES,
    Envelope,
    EnvelopeError,
    ErrorCode,
    decode,
    encode,
    handling,
    new_message_id,
)

VALID = {0: 1, 1: "msg.text", 2: bytes(16), 3: 1000}


def sample(**kw) -> Envelope:
    return Envelope(type="msg.text", body={"text": "hi Bob 👋"}, **kw)


def code_of(data: bytes) -> ErrorCode:
    with pytest.raises(EnvelopeError) as exc:
        decode(data)
    return exc.value.code


# --- round trips -----------------------------------------------------------


def test_roundtrip():
    env = sample(conversation_id=os.urandom(16), seq=42, reply_to=os.urandom(16), critical=True)
    out = decode(encode(env))
    assert out == env


def test_roundtrip_minimal():
    env = Envelope(type="session.ping")
    assert decode(encode(env)) == env


def test_encoding_is_deterministic():
    env = sample()
    assert encode(env) == encode(env)
    assert encode(env, pad=False) == encode(env, pad=False)


# --- padding ---------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 50, 150, 200, 240, 600, 1000, 3000, 10000, 40000])
def test_padding_hits_a_size_class(n):
    env = Envelope(type="msg.text", body={"text": "x" * n})
    data = encode(env)
    assert len(data) in PAD_CLASSES or len(data) == len(encode(env, pad=False))
    assert decode(data).body == env.body


def test_short_messages_all_look_the_same_size():
    sizes = {len(encode(Envelope(type="msg.text", body={"text": "a" * n}))) for n in range(100)}
    assert sizes == {256}


def test_too_large_to_encode():
    with pytest.raises(EnvelopeError) as exc:
        encode(Envelope(type="msg.text", body={"text": "x" * MAX_ENVELOPE}))
    assert exc.value.code == ErrorCode.TOO_LARGE


# --- validation ------------------------------------------------------------


def test_unsupported_version():
    data = cbor2.dumps({0: 2, 1: "x", 2: bytes(16), 3: 1})
    assert code_of(data) == ErrorCode.UNSUPPORTED_VERSION


@pytest.mark.parametrize("missing", [0, 1, 2, 3])
def test_missing_required_field(missing):
    m = dict(VALID)
    del m[missing]
    assert code_of(cbor2.dumps(m)) == ErrorCode.MALFORMED


@pytest.mark.parametrize(
    "fields",
    [
        {1: 5},  # type must be text
        {1: ""},  # empty type
        {1: "t" * 65},  # type too long
        {2: bytes(15)},  # id must be 16 bytes
        {2: "not bytes"},
        {3: -1},  # negative timestamp
        {3: True},  # bool is not an int here
        {3: "now"},
        {4: bytes(3)},  # conversation id must be 16 bytes
        {5: -2},  # negative seq
        {7: 1},  # critical must be a bool
        {8: "not a map"},
    ],
)
def test_wrong_field_values(fields):
    data = cbor2.dumps({**VALID, **fields})
    assert code_of(data) == ErrorCode.MALFORMED


def test_not_a_map():
    assert code_of(cbor2.dumps([1, 2, 3])) == ErrorCode.MALFORMED


def test_unknown_keys_are_ignored():
    data = cbor2.dumps({**VALID, 99: "future", 8: {}})
    assert decode(data).type == "msg.text"


# --- hostile input ---------------------------------------------------------


def test_garbage_never_raises_anything_but_envelope_error():
    rng = random.Random(1234)
    for _ in range(3000):
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        try:
            decode(junk)
        except EnvelopeError:
            pass


def test_mutated_valid_envelopes_never_crash():
    rng = random.Random(99)
    good = encode(sample(conversation_id=bytes(16), seq=1), pad=False)
    for _ in range(3000):
        b = bytearray(good)
        for _ in range(rng.randrange(1, 4)):
            b[rng.randrange(len(b))] = rng.randrange(256)
        try:
            decode(bytes(b))
        except EnvelopeError:
            pass


def test_invalid_utf8_text_rejected():
    # map(4): 0 -> 1, 1 -> text(2) holding the invalid bytes ff fe, 2 -> bytes(16), 3 -> 1
    data = bytes.fromhex("a4" "0001" "0162fffe" "025000000000000000000000000000000000" "0301")
    assert code_of(data) == ErrorCode.MALFORMED


def test_nesting_too_deep():
    deep: object = 1
    for _ in range(40):
        deep = [deep]
    assert code_of(cbor2.dumps({**VALID, 8: {"x": deep}})) == ErrorCode.MALFORMED


def test_absurdly_deep_nesting_does_not_crash():
    data = b"\x81" * 60_000 + b"\x00"  # [[[[ ... 0 ]]]], just under the size limit
    assert code_of(data) == ErrorCode.MALFORMED


def test_too_many_items():
    assert code_of(cbor2.dumps({**VALID, 8: {"a": list(range(1025))}})) == ErrorCode.MALFORMED


def test_cbor_tags_are_rejected():
    tagged = cbor2.CBORTag(4242, "sneaky")
    assert code_of(cbor2.dumps({**VALID, 8: {"x": tagged}})) == ErrorCode.MALFORMED
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)  # encodes as CBOR tag 0/1
    assert code_of(cbor2.dumps({**VALID, 8: {"x": when}})) == ErrorCode.MALFORMED


def test_oversized_input_rejected_before_parsing():
    assert code_of(b"\x00" * (MAX_ENVELOPE + 1)) == ErrorCode.TOO_LARGE


# --- unknown types ---------------------------------------------------------


def test_handling_rules():
    known = {"msg.text"}
    assert handling(Envelope(type="msg.text"), known) == "handle"
    assert handling(Envelope(type="msg.sticker"), known) == "ignore"
    assert handling(Envelope(type="msg.sticker", critical=True), known) == "unsupported"


# --- message ids -----------------------------------------------------------


def test_message_id_is_a_uuid7():
    mid = new_message_id()
    assert len(mid) == 16
    assert mid[6] >> 4 == 7  # version 7
    assert mid[8] >> 6 == 0b10  # RFC 4122 variant


def test_message_ids_sort_by_time_and_are_unique():
    a, b = new_message_id(1_000_000), new_message_id(1_000_001)
    assert a < b
    assert len({new_message_id() for _ in range(1000)}) == 1000