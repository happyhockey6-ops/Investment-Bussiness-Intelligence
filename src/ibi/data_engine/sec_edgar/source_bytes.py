"""Exact SEC response bytes: identity and Decimal-safe decoding.

Pure: no I/O. The bytes SEC returned are the evidence; everything else is
derived from them. `decode_sec_json` is the only sanctioned way to turn
those bytes into Python objects:

- non-integer numbers become `Decimal` straight from their JSON text, never
  passing through a binary float (`0.12345678` stays `Decimal("0.12345678")`,
  `1.10` keeps its written scale);
- integers stay exact Python `int`s of any size;
- the non-standard tokens `NaN`, `Infinity` and `-Infinity`, which Python's
  `json` module would otherwise accept, are rejected;
- the bytes must be valid UTF-8 (RFC 8259 §8.1).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal


class SourceDecodeError(ValueError):
    """The archived bytes are not a strict, Decimal-safe JSON document."""


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_constant(token: str) -> object:
    raise SourceDecodeError(f"non-standard JSON number {token!r} is not allowed")


def decode_sec_json(raw: bytes) -> object:
    if not isinstance(raw, bytes | bytearray):
        raise SourceDecodeError(f"expected bytes, got {type(raw).__name__}")
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError as e:
        raise SourceDecodeError(f"response is not valid UTF-8: {e}") from e
    if text.startswith("﻿"):
        raise SourceDecodeError("response starts with a byte-order mark")
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise SourceDecodeError(f"response is not valid JSON: {e}") from e
