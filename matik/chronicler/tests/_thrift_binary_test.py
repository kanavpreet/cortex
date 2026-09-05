"""Tests for the minimal Thrift binary protocol decoder."""

import struct

import pytest

from chronicler.models._thrift_binary import (
    T_BOOL,
    T_BYTE,
    T_DOUBLE,
    T_I16,
    T_I32,
    T_I64,
    T_LIST,
    T_MAP,
    T_SET,
    T_STOP,
    T_STRING,
    T_STRUCT,
    ThriftDecodeError,
    _Reader,
    read_struct,
)
from chronicler.tests._thrift_encoder import encode_struct


class TestReader:
    def test_take_raises_on_truncation(self) -> None:
        r = _Reader(b"\x00")
        with pytest.raises(ThriftDecodeError, match="truncated"):
            r._take(5)

    def test_signed_unsigned_byte_reads(self) -> None:
        # -1 signed (0xFF), 255 unsigned
        r = _Reader(b"\xff")
        assert r.read_i8() == -1
        r = _Reader(b"\xff")
        assert r.read_u8() == 255

    def test_negative_binary_length_raises(self) -> None:
        # i32 length = -1
        r = _Reader(struct.pack(">i", -1))
        with pytest.raises(ThriftDecodeError, match="negative binary length"):
            r.read_binary()

    def test_skip_unknown_type_raises(self) -> None:
        r = _Reader(b"")
        with pytest.raises(ThriftDecodeError, match="unknown thrift type id"):
            r.skip_value(99)


class TestReadStructPrimitives:
    """Cover the value-decoding branches of read_struct for each supported type."""

    def test_decodes_i16_field(self) -> None:
        # Field 5, type I16, value 1234.
        buf = bytes([T_I16]) + struct.pack(">h", 5) + struct.pack(">h", 1234)
        buf += bytes([T_STOP])
        assert read_struct(buf)[5] == (T_I16, 1234)

    def test_decodes_i32_field(self) -> None:
        buf = bytes([T_I32]) + struct.pack(">h", 2) + struct.pack(">i", -42)
        buf += bytes([T_STOP])
        assert read_struct(buf)[2] == (T_I32, -42)

    def test_decodes_bool_and_byte_fields(self) -> None:
        buf = bytes([T_BOOL]) + struct.pack(">h", 1) + b"\x01"
        buf += bytes([T_BYTE]) + struct.pack(">h", 2) + b"\xff"
        buf += bytes([T_STOP])
        out = read_struct(buf)
        assert out[1] == (T_BOOL, True)
        assert out[2] == (T_BYTE, -1)

    def test_decodes_double_field(self) -> None:
        buf = bytes([T_DOUBLE]) + struct.pack(">h", 1) + struct.pack(">d", 3.5)
        buf += bytes([T_STOP])
        assert read_struct(buf)[1] == (T_DOUBLE, 3.5)

    def test_decodes_string_field(self) -> None:
        out = read_struct(encode_struct([(1, T_STRING, "hello")]))
        assert out[1] == (T_STRING, b"hello")

    def test_decodes_string_string_map(self) -> None:
        out = read_struct(encode_struct([(6, T_MAP, {"a": "1", "b": "2"})]))
        assert out[6] == (T_MAP, {"a": "1", "b": "2"})

    def test_skips_unsupported_map_kinds(self) -> None:
        # map<i32, i32> with one entry — decoder must skip both values.
        body = bytearray()
        body.append(T_MAP)
        body += struct.pack(">h", 7)  # field id
        body.append(T_I32)  # key type
        body.append(T_I32)  # val type
        body += struct.pack(">i", 1)  # size
        body += struct.pack(">i", 100)  # key
        body += struct.pack(">i", 200)  # val
        body.append(T_STOP)
        out = read_struct(bytes(body))
        assert out[7] == (T_MAP, None)

    def test_skips_list_and_set_fields(self) -> None:
        # list<i32> with 2 elements.
        body = bytearray()
        for ftype in (T_LIST, T_SET):
            body.append(ftype)
            body += struct.pack(">h", 1)
            body.append(T_I32)
            body += struct.pack(">i", 2)
            body += struct.pack(">i", 10)
            body += struct.pack(">i", 20)
        body.append(T_STOP)
        out = read_struct(bytes(body))
        assert out[1] == (T_SET, None)  # last write wins on same field id

    def test_skips_nested_struct_field(self) -> None:
        inner = encode_struct([(1, T_STRING, "ignored")])
        # Manually splice as a struct field (T_STRUCT) at field id 8.
        body = bytearray()
        body.append(T_STRUCT)
        body += struct.pack(">h", 8)
        body += inner  # inner already has its own STOP terminator
        body.append(T_STOP)
        out = read_struct(bytes(body))
        assert out[8] == (T_STRUCT, None)


class TestSkipValueDirect:
    """Exercise skip_value paths that aren't reached via read_struct fields."""

    def test_skip_bool_byte(self) -> None:
        r = _Reader(b"\x01\xff")
        r.skip_value(T_BOOL)
        r.skip_value(T_BYTE)
        assert r.pos == 2

    def test_skip_i16(self) -> None:
        r = _Reader(struct.pack(">h", 5))
        r.skip_value(T_I16)
        assert r.pos == 2

    def test_skip_i32(self) -> None:
        r = _Reader(struct.pack(">i", 5))
        r.skip_value(T_I32)
        assert r.pos == 4

    def test_skip_i64_and_double(self) -> None:
        r = _Reader(struct.pack(">qd", 5, 1.5))
        r.skip_value(T_I64)
        r.skip_value(T_DOUBLE)
        assert r.pos == 16

    def test_skip_string(self) -> None:
        r = _Reader(struct.pack(">i", 3) + b"abc")
        r.skip_value(T_STRING)
        assert r.pos == 7

    def test_skip_struct_with_nested_fields(self) -> None:
        # Struct containing one I32 field then STOP.
        body = (
            bytes([T_I32])
            + struct.pack(">h", 1)
            + struct.pack(">i", 9)
            + bytes([T_STOP])
        )
        r = _Reader(body)
        r.skip_value(T_STRUCT)
        assert r.pos == len(body)

    def test_skip_map_with_complex_values(self) -> None:
        # map<string, string> with 1 entry
        body = bytearray()
        body.append(T_STRING)
        body.append(T_STRING)
        body += struct.pack(">i", 1)
        body += struct.pack(">i", 1) + b"k"
        body += struct.pack(">i", 1) + b"v"
        r = _Reader(bytes(body))
        r.skip_value(T_MAP)
        assert r.pos == len(body)

    def test_skip_list_of_i64(self) -> None:
        body = bytes([T_I64]) + struct.pack(">i", 2) + struct.pack(">qq", 1, 2)
        r = _Reader(body)
        r.skip_value(T_LIST)
        assert r.pos == len(body)
