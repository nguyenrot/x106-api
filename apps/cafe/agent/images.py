"""Find + verify a real cover photo for an agent review — best-effort.

The agent (agy web search) proposes image URLs it actually saw on pages about
the cafe (`image_candidates`). We then:

1. download each candidate (size/type capped),
2. reject thumbnails/logos by dimension + aspect checks,
3. ask Gemini vision (GEMINI_API_KEY already on the VPS for the console app)
   whether the photo really shows a cafe matching the review's description,
4. re-host the first passing photo through the existing `store_image`
   optimizer → GitHub → jsDelivr CDN pipeline.

Strict by design: no verified photo → no cover (the frontend fallback is fine).
Never raises out of `find_cover` — a cover must never block publishing.
"""

from __future__ import annotations

import io
import json
import logging
import time

import httpx
from django.conf import settings
from PIL import Image

from apps.core.uploads import store_image

log = logging.getLogger("apps.cafe.agent.images")

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MIN_W, MIN_H = 640, 420
ASPECT_RANGE = (0.6, 2.6)  # reject banners/strips; covers render ~16:10
DOWNLOAD_UA = (
    "Mozilla/5.0 (compatible; cafe.kynguyen.cc cover-fetcher; phamkynguyen753@gmail.com)"
)

_VERIFY_PROMPT = """Bạn đang kiểm duyệt ảnh bìa cho một bài giới thiệu quán cà phê ở Đà Nẵng.

Quán: {name} — khu vực {district}.
Mô tả bài viết: {excerpt}

Ảnh đính kèm có ĐẠT làm ảnh bìa không? Đạt = ảnh chụp thực tế (không phải logo,
menu chữ, bản đồ, ảnh render 3D, chân dung cá nhân, watermark che kín) và nội
dung là không gian/đồ uống/mặt tiền một quán cà phê phù hợp mô tả trên.

Trả về đúng một JSON object: {{"match": true/false, "reason": "ngắn gọn"}}"""


def _download(url: str) -> tuple[bytes, str] | None:
    """Fetch an image URL. Returns (bytes, mime) or None."""
    try:
        with httpx.Client(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": DOWNLOAD_UA}
        ) as c:
            resp = c.get(url)
            resp.raise_for_status()
            raw = resp.content
    except Exception as exc:
        log.info("cover candidate download failed %s: %s", url, exc)
        return None
    if not raw or len(raw) > MAX_DOWNLOAD_BYTES:
        log.info("cover candidate rejected (size %s bytes): %s", len(raw or b""), url)
        return None
    mime = (resp.headers.get("content-type") or "").split(";")[0].strip() or "image/jpeg"
    return raw, mime


def _dimensions_ok(raw: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
    except Exception:
        return False
    if w < MIN_W or h < MIN_H:
        return False
    aspect = w / h
    return ASPECT_RANGE[0] <= aspect <= ASPECT_RANGE[1]


def _gemini_verify(raw: bytes, mime: str, *, name: str, district: str, excerpt: str) -> bool:
    """True only when Gemini confirms the photo matches. Missing key / API error
    → False: the user's requirement is verify-then-attach, so unverified images
    never ship. Transient 503/429 spikes get one retry — they killed an
    otherwise-good candidate on the Lighthouse run."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        log.warning("GEMINI_API_KEY unset — cannot verify cover photos, skipping cover")
        return False

    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    contents = [
        genai_types.Part.from_bytes(data=raw, mime_type=mime),
        _VERIFY_PROMPT.format(name=name, district=district, excerpt=excerpt),
    ]
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
            text = (resp.text or "").strip()
            start, end = text.find("{"), text.rfind("}")
            verdict = json.loads(text[start : end + 1])
            if not verdict.get("match"):
                log.info("gemini rejected cover for %s: %s", name, verdict.get("reason"))
            return bool(verdict.get("match"))
        except genai_errors.APIError as exc:
            if attempt == 1 and getattr(exc, "code", None) in (429, 503):
                log.info("gemini transient %s — retrying once in 12s", exc.code)
                time.sleep(12)
                continue
            log.warning("gemini cover verify failed (%s) — skipping candidate", exc)
            return False
        except Exception as exc:
            log.warning("gemini cover verify failed (%s) — skipping candidate", exc)
            return False
    return False


def find_cover(
    candidates: list[dict],
    *,
    name: str,
    district: str,
    excerpt: str,
) -> dict | None:
    """First candidate that downloads, passes dimension checks and Gemini
    verification → re-hosted CDN URL. Returns {"url", "source_page"} or None."""
    for cand in candidates[:4]:
        url = cand.get("url", "")
        fetched = _download(url)
        if fetched is None:
            continue
        raw, mime = fetched
        if not _dimensions_ok(raw):
            log.info("cover candidate rejected (dimensions): %s", url)
            continue
        if not _gemini_verify(raw, mime, name=name, district=district, excerpt=excerpt):
            continue
        try:
            meta = store_image(raw, prefix="cafe", max_dim=1600)
        except Exception as exc:
            log.warning("store_image failed for %s: %s", url, exc)
            continue
        stored = meta["url"]
        # Local-storage fallback returns a relative /media path that only
        # resolves on the API host — absolutize it (jsDelivr URLs pass through).
        if stored.startswith("/"):
            stored = f"https://api.kynguyen.cc{stored}"
        log.info("cover accepted for %s: %s (from %s)", name, stored, cand.get("page") or url)
        return {"url": stored, "source_page": cand.get("page", "")}
    return None
