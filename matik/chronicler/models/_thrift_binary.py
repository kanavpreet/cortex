"""Minimal Thrift binary protocol reader (TBinaryProtocol).

Pure-Python decoder for the subset of Thrift binary needed to unwrap the Jitney
`RawMessage` envelope and the inner Yoyo `CallbackEvent` payload. We do NOT
depend on the `thrift` package or pre-generated bindings — the wire format is
small enough to handle in ~100 lines and avoids a build-time codegen step.

Wire format (TBinaryProtocol, big-endian):
    struct = field* STOP
    field  = type:i8 id:i16 value
    types: STOP=0, BOOL=2, BYTE=3, DOUBLE=4, I16=6, I32=8, I64=10,
           STRING/BINARY=11, STRUCT=12, MAP=13, SET=14, LIST=15

Reference: https://github.com/apache/thrift/blob/master/doc/specs/thrift-binary-protocol.md
"""

from __future__ import annotations

import struct
from typing import Any

T_STOP = 0
T_BOOL = 2
T_BYTE = 3
T_DOUBLE = 4
T_I16 = 6
T_I32 = 8
T_I64 = 10
T_STRING = 11  # also used for binary
T_STRUCT = 12
T_MAP = 13
T_SET = 14
T_LIST = 15


class ThriftDecodeError(Exception):
    """Raised when a Thrift binary blob is malformed or truncated."""


class _Reader:
    """Cursor over a bytes buffer with big-endian Thrift primitive readers."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.buf):
            raise ThriftDecodeError(
                f"truncated: need {n} bytes at offset {self.pos}, "
                f"have {len(self.buf) - self.pos}"
            )
        chunk = self.buf[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def read_i8(self) -> int:
        return int(struct.unpack_from(">b", self._take(1))[0])

    def read_u8(self) -> int:
        return int(struct.unpack_from(">B", self._take(1))[0])

    def read_i16(self) -> int:
        return int(struct.unpack_from(">h", self._take(2))[0])

    def read_i32(self) -> int:
        return int(struct.unpack_from(">i", self._take(4))[0])

    def read_i64(self) -> int:
        return int(struct.unpack_from(">q", self._take(8))[0])

    def read_double(self) -> float:
        return float(struct.unpack_from(">d", self._take(8))[0])

    def read_binary(self) -> bytes:
        n = self.read_i32()
        if n < 0:
            raise ThriftDecodeError(f"negative binary length {n}")
        return self._take(n)

    def read_string(self) -> str:
        return self.read_binary().decode("utf-8")

    def skip_value(self, type_id: int) -> None:
        """Advance the cursor past a value of the given Thrift type."""
        if type_id in (T_BOOL, T_BYTE):
            self._take(1)
        elif type_id == T_I16:
            self._take(2)
        elif type_id == T_I32:
            self._take(4)
        elif type_id in (T_I64, T_DOUBLE):
            self._take(8)
        elif type_id == T_STRING:
            self.read_binary()
        elif type_id == T_STRUCT:
            while True:
                ft = self.read_u8()
                if ft == T_STOP:
                    break
                self.read_i16()  # field id
                self.skip_value(ft)
        elif type_id == T_MAP:
            key_t = self.read_u8()
            val_t = self.read_u8()
            size = self.read_i32()
            for _ in range(size):
                self.skip_value(key_t)
                self.skip_value(val_t)
        elif type_id in (T_LIST, T_SET):
            elem_t = self.read_u8()
            size = self.read_i32()
            for _ in range(size):
                self.skip_value(elem_t)
        else:
            raise ThriftDecodeError(f"unknown thrift type id {type_id}")


def read_struct(buf: bytes) -> dict[int, tuple[int, Any]]:
    """Read one Thrift struct from the start of buf.

    Returns a dict mapping field_id -> (type_id, value). Only the value types
    we actually need are decoded (i32, i64, string, binary, map<string,string>);
    all other types are skipped and recorded as their type with value=None.
    """
    r = _Reader(buf)
    fields: dict[int, tuple[int, Any]] = {}
    while True:
        ft = r.read_u8()
        if ft == T_STOP:
            break
        fid = r.read_i16()
        value: Any
        if ft == T_STRING:
            value = r.read_binary()
        elif ft == T_I32:
            value = r.read_i32()
        elif ft == T_I64:
            value = r.read_i64()
        elif ft == T_I16:
            value = r.read_i16()
        elif ft == T_BOOL:
            value = r.read_u8() != 0
        elif ft == T_BYTE:
            value = r.read_i8()
        elif ft == T_DOUBLE:
            value = r.read_double()
        elif ft == T_MAP:
            key_t = r.read_u8()
            val_t = r.read_u8()
            size = r.read_i32()
            if key_t == T_STRING and val_t == T_STRING:
                m: dict[str, str] = {}
                for _ in range(size):
                    k = r.read_string()
                    v = r.read_string()
                    m[k] = v
                value = m
            else:
                # Map with non-string key/value types: skip the entries.
                for _ in range(size):
                    r.skip_value(key_t)
                    r.skip_value(val_t)
                value = None
        else:
            r.skip_value(ft)
            value = None
        fields[fid] = (ft, value)
    return fields
