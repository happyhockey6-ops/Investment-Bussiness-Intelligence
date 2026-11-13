"""Decimal-safe, strict decoding of exact SEC response bytes."""

from decimal import Decimal

import pytest

from ibi.data_engine.sec_edgar.source_bytes import SourceDecodeError, decode_sec_json, sha256_hex


def test_numbers_never_pass_through_float():
    doc = decode_sec_json(b'{"a": 0.12345678, "b": 1.10, "c": 123456789012345678901, '
                          b'"d": 0.1, "e": 1E+2, "f": -0.000000001}')
    assert doc["a"] == Decimal("0.12345678") and isinstance(doc["a"], Decimal)
    assert str(doc["b"]) == "1.10"  # written scale preserved
    assert doc["c"] == 123456789012345678901 and isinstance(doc["c"], int)
    assert str(doc["d"]) == "0.1"  # a float would be 0.1000000000000000055...
    assert doc["e"] == Decimal(100)
    assert doc["f"] == Decimal("-1E-9")


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_standard_numbers_are_rejected(token):
    with pytest.raises(SourceDecodeError, match="non-standard"):
        decode_sec_json(b'{"val": ' + token + b"}")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"name": "\xff"}', "UTF-8"),
        (b"\xef\xbb\xbf{}", "byte-order mark"),
        (b'{"a": 1', "not valid JSON"),
        (b"", "not valid JSON"),
    ],
)
def test_invalid_documents_are_rejected(raw, message):
    with pytest.raises(SourceDecodeError, match=message):
        decode_sec_json(raw)


def test_only_bytes_are_accepted():
    with pytest.raises(SourceDecodeError, match="expected bytes"):
        decode_sec_json('{"a": 1}')  # type: ignore[arg-type]


def test_sha256_is_of_the_exact_bytes():
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex(b'{"a":1}') != sha256_hex(b'{"a": 1}')
