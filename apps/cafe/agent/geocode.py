"""Forward-geocode a Đà Nẵng street address via Nominatim (OSM).

Free and keyless. The agent runs once a day, far inside Nominatim's fair-use
policy; the policy's hard requirements are a meaningful User-Agent and ≤1
request/second — both honored here. A miss returns None and the review simply
publishes without coordinates (the frontend map falls back to a text query).
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("apps.cafe.agent.geocode")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "cafe.kynguyen.cc review-agent (phamkynguyen753@gmail.com)"

# Đà Nẵng bounding box — reject matches that land in another city (Nominatim
# happily resolves an ambiguous street name to Hà Nội/HCMC otherwise).
LAT_RANGE = (15.85, 16.35)
LNG_RANGE = (107.85, 108.55)


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
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]):
        log.info("geocode hit outside Đà Nẵng bbox (%s, %s) for %r — discarded", lat, lng, q)
        return None
    return lat, lng


def geocode_da_nang(address: str, name: str = "") -> tuple[float, float] | None:
    """Best-effort (lat, lng) for a cafe. Tries address, then name+address."""
    candidates = []
    if address:
        candidates.append(f"{address}, Đà Nẵng, Việt Nam")
    if name:
        candidates.append(f"{name}, {address}, Đà Nẵng, Việt Nam" if address else f"{name}, Đà Nẵng, Việt Nam")

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
