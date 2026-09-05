"""Minimal Thrift binary encoder for test fixtures.

Mirrors only what tests need to round-trip through the chronicler decoder:
strings (treated as binary), i64, and map<string,string>. STOP terminator
included automatically by `encode_struct`.
"""

from __future__ import annotations

import struct

from chronicler.models._thrift_binary import (
    T_I64,
    T_MAP,
    T_STOP,
    T_STRING,
)


def _i16(v: int) -> bytes:
    return struct.pack(">h", v)


def _i32(v: int) -> bytes:
    return struct.pack(">i", v)


def _i64(v: int) -> bytes:
    return struct.pack(">q", v)


def _binary(b: bytes) -> bytes:
    return _i32(len(b)) + b


def _string(s: str) -> bytes:
    return _binary(s.encode("utf-8"))


def encode_struct(fields: list[tuple[int, int, object]]) -> bytes:
    """Encode a Thrift struct from (field_id, type_id, value) tuples.

    Supported types: T_STRING (str or bytes), T_I64 (int), T_MAP (dict[str,str]).
    """
    out = bytearray()
    for fid, ftype, value in fields:
        out.append(ftype)
        out += _i16(fid)
        if ftype == T_STRING:
            if isinstance(value, str):
                out += _string(value)
            elif isinstance(value, bytes):
                out += _binary(value)
            else:
                raise TypeError(
                    f"T_STRING value must be str or bytes, got {type(value)}"
                )
        elif ftype == T_I64:
            assert isinstance(value, int)
            out += _i64(value)
        elif ftype == T_MAP:
            assert isinstance(value, dict)
            out.append(T_STRING)  # key type
            out.append(T_STRING)  # val type
            out += _i32(len(value))
            for k, v in value.items():
                out += _string(k)
                out += _string(v)
        else:
            raise NotImplementedError(f"encoding type {ftype} not supported in tests")
    out.append(T_STOP)
    return bytes(out)
