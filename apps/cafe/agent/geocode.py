"""Forward-geocode a Đà Nẵng street address via Nominatim (OSM).

Free and keyless. The agent runs once a day, far inside Nominatim's fair-use
policy; the policy's hard requirements are a meaningful User-Agent and ≤1
request/second — both honored here. A miss returns None and the review simply
publishes without coordinates (the frontend map falls back to a text query).
"""

from __future__ import annotations

import logging
import re
import time

import httpx

log = logging.getLogger("apps.cafe.agent.geocode")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "cafe.kynguyen.cc review-agent (phamkynguyen753@gmail.com)"

# Đà Nẵng bounding box — reject matches that land in another city (Nominatim
# happily resolves an ambiguous street name to Hà Nội/HCMC otherwise).
LAT_RANGE = (15.85, 16.35)
LNG_RANGE = (107.85, 108.55)


def in_da_nang(lat: float, lng: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]


def _query(q: str) -> tuple[float, float] | None:
    resp = httpx.get(
        NOMINATIM_URL,
        params={"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "vn"},
        headers={"User-Agent": USER_AGENT},
        timeout=15.0,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    lat, lng = float(rows[0]["lat"]), float(rows[0]["lon"])
    if not in_da_nang(lat, lng):
        log.info("geocode hit outside Đà Nẵng bbox (%s, %s) for %r — discarded", lat, lng, q)
        return None
    return lat, lng


_STREET_ABBR = re.compile(r"\b[DĐđ]\.\s*")  # "D. Đình Nghệ" / "Đ. Lê Lợi" → bare street name
_HOUSE_NO = re.compile(r"^\s*\d+[A-Za-z]?(?:[/-]\w+)*\s+")


def _normalize(address: str) -> str:
    """Strip the 'Đường' abbreviation Nominatim chokes on (agy writes 'D. X')."""
    return _STREET_ABBR.sub("", address).strip()


def geocode_da_nang(address: str, name: str = "") -> tuple[float, float] | None:
    """Best-effort (lat, lng). Tries the full address, then street-without-house-
    number (OSM VN coverage often lacks house numbers), then the cafe name."""
    candidates: list[str] = []
    norm = _normalize(address) if address else ""
    if norm:
        candidates.append(f"{norm}, Đà Nẵng, Việt Nam")
        street = _HOUSE_NO.sub("", norm.split(",")[0]).strip()
        rest = ", ".join(p.strip() for p in norm.split(",")[1:] if p.strip())
        if street and street != norm:
            candidates.append(f"{street}, {rest}, Đà Nẵng, Việt Nam" if rest else f"{street}, Đà Nẵng, Việt Nam")
    if name:
        candidates.append(f"{name}, Đà Nẵng, Việt Nam")

    for i, q in enumerate(candidates):
        try:
            hit = _query(q)
        except Exception as exc:  # network/HTTP — never block publishing on geocode
            log.warning("nominatim query failed for %r: %s", q, exc)
            return None
        if hit:
            return hit
        if i < len(candidates) - 1:
            time.sleep(1.1)  # Nominatim absolute max 1 req/s
    return None
