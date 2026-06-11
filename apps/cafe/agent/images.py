"""Find + verify a real cover photo for an agent review — best-effort.

The agent (agy web search) proposes image URLs it actually saw on pages about
the cafe (`image_candidates`). We then:

1. ask agy (one extra invocation) to OPEN each candidate and approve only real
   photos of a cafe matching the review — no Gemini API key involved, the agy
   CLI runs on its own Antigravity account,
2. download the approved candidates in agy's preferred order (size/type capped),
3. reject thumbnails/logos by dimension + aspect checks,
4. re-host the first passing photo through the existing `store_image`
   optimizer → GitHub → jsDelivr CDN pipeline.

Strict by design: no verified photo → no cover (the frontend fallback is fine).
Never raises out of `find_cover` — a cover must never block publishing.
"""

from __future__ import annotations

import io
import logging

import httpx
from PIL import Image

from apps.core.uploads import store_image

from .agy import AgyError, run_agy

log = logging.getLogger("apps.cafe.agent.images")

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MIN_W, MIN_H = 640, 420
ASPECT_RANGE = (0.6, 2.6)  # reject banners/strips; covers render ~16:10
DOWNLOAD_UA = (
    "Mozilla/5.0 (compatible; cafe.kynguyen.cc cover-fetcher; phamkynguyen753@gmail.com)"
)

_PICK_PROMPT = """Bạn là biên tập viên ảnh của blog cafe.kynguyen.cc. Hãy MỞ XEM từng ảnh dưới đây (dùng khả năng xem web/ảnh của bạn) và chọn ảnh bìa.

Quán: {name} — khu vực {district}, Đà Nẵng.
Mô tả bài viết: {excerpt}

Danh sách ảnh ứng viên (đánh số từ 0):
{candidates_block}

Tiêu chí ĐẠT: ảnh chụp thực tế (không phải logo, menu chữ, bản đồ, ảnh render
3D, chân dung cá nhân, ảnh watermark che kín) và nội dung là không gian /
mặt tiền / đồ uống của một quán cà phê phù hợp mô tả trên.

Trả về đúng MỘT JSON object, không giải thích gì thêm:
{{"approved": [các index ĐẠT, xếp ảnh hợp làm bìa nhất lên trước], "reason": "ngắn gọn"}}
Không ảnh nào đạt → {{"approved": [], "reason": "..."}}"""


def _agy_approve(candidates: list[dict], *, name: str, district: str, excerpt: str) -> list[int]:
    """One agy call vets the whole candidate list. Returns approved indices in
    preference order; [] on agy failure (strict: unverified images never ship)."""
    block = "\n".join(
        f"{i}. {c['url']}" + (f" (thấy trên trang: {c['page']})" if c.get("page") else "")
        for i, c in enumerate(candidates)
    )
    prompt = _PICK_PROMPT.format(
        name=name, district=district, excerpt=excerpt, candidates_block=block
    )
    try:
        result = run_agy(prompt, timeout_sec=240)
    except AgyError as exc:
        log.warning("agy cover vetting failed (%s) — skipping cover", exc)
        return []
    raw = result.parsed.get("approved")
    if not isinstance(raw, list):
        log.warning("agy cover vetting returned no 'approved' list: %r", result.parsed)
        return []
    approved = [i for i in raw if isinstance(i, int) and 0 <= i < len(candidates)]
    if not approved:
        log.info("agy rejected all cover candidates for %s: %s", name, result.parsed.get("reason"))
    return approved


def _download(url: str) -> bytes | None:
    """Fetch an image URL. Returns bytes or None."""
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
    return raw


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


def find_cover(
    candidates: list[dict],
    *,
    name: str,
    district: str,
    excerpt: str,
) -> dict | None:
    """agy vets the list once, then the first approved candidate that downloads
    and passes dimension checks is re-hosted. Returns {"url", "source_page"}
    or None."""
    candidates = candidates[:4]
    if not candidates:
        return None

    approved = _agy_approve(candidates, name=name, district=district, excerpt=excerpt)
    for idx in approved:
        cand = candidates[idx]
        url = cand.get("url", "")
        raw = _download(url)
        if raw is None:
            continue
        if not _dimensions_ok(raw):
            log.info("cover candidate rejected (dimensions): %s", url)
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
