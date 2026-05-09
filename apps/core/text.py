"""Rune-aware text utilities. Mirrors clampRunes/cleanText from the Go service.

Python `len(str)` counts Unicode code points, which is the same notion of
"runes" the Go code uses, so we don't need any extra cleverness here."""


def clamp_runes(value: str | None, max_chars: int) -> str:
    if value is None or max_chars <= 0:
        return ""
    s = value.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


def clamp_string(value: str | None, max_chars: int) -> str:
    if value is None:
        return ""
    s = value.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars]
