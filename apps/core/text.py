"""Rune-aware text utilities. Mirrors clampRunes/cleanText from the Go service.

Python `len(str)` counts Unicode code points, which is the same notion of
"runes" the Go code uses, so we don't need any extra cleverness here."""

import re
import unicodedata

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify_vi(value: str | None, *, max_chars: int = 200) -> str:
    """ASCII slug that transliterates Vietnamese diacritics.

    Django's stock slugify drops non-ASCII letters, so "Cà Phê Sỏi Đá" would
    collapse to "c-ph-si". We normalize NFD + strip combining marks and special
    case đ/Đ first, yielding "ca-phe-soi-da".
    """
    if not value:
        return ""
    s = value.strip().lower().replace("đ", "d").replace("Đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _SLUG_STRIP_RE.sub("-", s).strip("-")
    return s[:max_chars].strip("-")


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
