"""Minimal Base58 (Bitcoin alphabet), no checksum."""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: index for index, char in enumerate(_ALPHABET)}


def b58encode(data: bytes) -> str:
    zeros = 0
    for byte in data:
        if byte == 0:
            zeros += 1
        else:
            break
    number = int.from_bytes(data, "big") if data else 0
    chars: list[str] = []
    while number:
        number, rem = divmod(number, 58)
        chars.append(_ALPHABET[rem])
    body = "".join(reversed(chars)) if chars else ""
    return "1" * zeros + body


def b58decode(text: str) -> bytes:
    if text == "":
        return b""
    zeros = 0
    for char in text:
        if char == "1":
            zeros += 1
        else:
            break
    number = 0
    for char in text:
        try:
            digit = _INDEX[char]
        except KeyError:
            raise ValueError(f"invalid base58 character: {char!r}") from None
        number = number * 58 + digit
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * zeros + raw
