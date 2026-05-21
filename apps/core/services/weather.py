"""Open-Meteo weather fetch for Đà Nẵng (Mỹ Khê beach).

Single in-process TTL cache. With N gunicorn workers each worker holds
its own copy — for a 15-min TTL on a free API that allows 10k/day, the
extra calls are negligible. If we ever scale or need cross-process
freshness, swap to Django's RedisCache backend.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Mỹ Khê beach, Đà Nẵng
LAT = 16.05
LON = 108.21
PLACE_LABEL = "Mỹ Khê"

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
CACHE_TTL_SEC = 15 * 60   # 15 minutes
REQUEST_TIMEOUT_SEC = 6.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "data": None}


# WMO weather code → bilingual short description.
# https://open-meteo.com/en/docs (Weather variable documentation)
_WMO: dict[int, tuple[str, str]] = {
    0:  ("clear",                  "trời quang"),
    1:  ("mostly clear",           "trời gần quang"),
    2:  ("partly cloudy",          "có mây rải rác"),
    3:  ("overcast",               "u ám"),
    45: ("fog",                    "sương mù"),
    48: ("freezing fog",           "sương mù lạnh"),
    51: ("light drizzle",          "mưa phùn nhẹ"),
    53: ("drizzle",                "mưa phùn"),
    55: ("heavy drizzle",          "mưa phùn dày"),
    56: ("freezing drizzle",       "mưa phùn đóng băng"),
    57: ("heavy freezing drizzle", "mưa phùn đóng băng dày"),
    61: ("light rain",             "mưa nhẹ"),
    63: ("rain",                   "mưa vừa"),
    65: ("heavy rain",             "mưa to"),
    66: ("freezing rain",          "mưa đóng băng"),
    67: ("heavy freezing rain",    "mưa đóng băng to"),
    71: ("light snow",             "tuyết nhẹ"),
    73: ("snow",                   "tuyết"),
    75: ("heavy snow",             "tuyết to"),
    77: ("snow grains",            "hạt tuyết"),
    80: ("light showers",          "mưa rào nhẹ"),
    81: ("showers",                "mưa rào"),
    82: ("heavy showers",          "mưa rào to"),
    85: ("light snow showers",     "tuyết rào nhẹ"),
    86: ("snow showers",           "tuyết rào"),
    95: ("thunderstorm",           "dông"),
    96: ("thunderstorm + hail",    "dông kèm mưa đá"),
    99: ("severe thunderstorm",    "dông to kèm mưa đá"),
}


def _describe(code: int) -> tuple[str, str]:
    return _WMO.get(code, ("weather", "thời tiết"))


def _fetch_remote() -> dict[str, Any]:
    """Hit Open-Meteo. Raises on network/HTTP failure."""
    params = urllib.parse.urlencode({
        "latitude":  LAT,
        "longitude": LON,
        "current":   "temperature_2m,weather_code",
        "timezone":  "Asia/Ho_Chi_Minh",
    })
    url = f"{ENDPOINT}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "x106-api/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        payload = json.loads(r.read().decode("utf-8"))

    current = payload.get("current") or {}
    code = int(current.get("weather_code", 0))
    temp = round(float(current.get("temperature_2m", 0)))
    desc_en, desc_vi = _describe(code)

    return {
        "temp_c":      temp,
        "code":        code,
        "description": {"en": desc_en, "vi": desc_vi},
        "place":       PLACE_LABEL,
    }


def get_weather() -> dict[str, Any]:
    """Return cached or freshly-fetched weather. Never raises — falls back
    to the last successful payload (or a static placeholder) on error."""
    now = time.time()
    with _lock:
        cached = _cache.get("data")
        ts = _cache.get("ts", 0.0)
        if cached is not None and (now - ts) < CACHE_TTL_SEC:
            return {**cached, "cached": True, "fetched_at": ts}

    try:
        fresh = _fetch_remote()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        # network / parse failure — return the last known value if we have it,
        # otherwise a static fallback so the frontend always renders something.
        if cached is not None:
            return {**cached, "cached": True, "stale": True, "fetched_at": ts}
        return {
            "temp_c":      None,
            "code":        None,
            "description": {"en": "weather unavailable", "vi": "không lấy được thời tiết"},
            "place":       PLACE_LABEL,
            "cached":      False,
            "error":       type(e).__name__,
        }

    with _lock:
        _cache["data"] = fresh
        _cache["ts"] = now
    return {**fresh, "cached": False, "fetched_at": now}
