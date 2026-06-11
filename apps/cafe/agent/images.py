"""Find + verify a real cover photo for an agent review — best-effort.

Image hunting runs as its OWN agy session (`search_cover_candidates`), separate
from the article-writing session: when both jobs shared one prompt the model
consistently prioritized the article and returned an empty candidate list. The
dedicated session both finds and vets the photos (it must open each image and
confirm it shows the right cafe), so the backend only has to:

1. download the candidates in the agent's preferred order (size capped),
2. reject thumbnails/logos by dimension + aspect checks,
3. re-host the first passing photo through the existing `store_image`
   optimizer → GitHub → jsDelivr CDN pipeline.

No Gemini API key involved anywhere — the agy CLI runs on its own Antigravity
account. Strict by design: nothing verified → no cover (frontend fallback is
fine). `find_cover` never raises — a cover must never block publishing.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import httpx
from PIL import Image

from apps.core.uploads import store_image

from .agy import AgyError, run_agy
from .validate import parse_coords, parse_image_candidates, parse_rating


@dataclass
class CoverSearch:
    """Everything one cover-hunt agy session can pick up about the cafe."""

    candidates: list[dict] = field(default_factory=list)
    coords: tuple[float, float] | None = None
    rating: tuple[float, str] | None = None  # (score, source)

log = logging.getLogger("apps.cafe.agent.images")

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MIN_W, MIN_H = 640, 420
ASPECT_RANGE = (0.6, 2.6)  # reject banners/strips; covers render ~16:10
DOWNLOAD_UA = (
    "Mozilla/5.0 (compatible; cafe.kynguyen.cc cover-fetcher; phamkynguyen753@gmail.com)"
)

_SEARCH_PROMPT = """Bạn là trợ lý tìm ảnh cho blog cafe.kynguyen.cc. Dùng web search.

Nhiệm vụ: tìm 2–4 URL ảnh chụp THẬT của ĐÚNG quán cà phê sau ở Đà Nẵng:

- Tên quán: {name}
- Địa chỉ: {address}
- Khu vực: {district}
{known_block}
Quy tắc:
- Chỉ lấy ảnh bạn thực sự thấy trên trang công khai viết về đúng quán này
  (bài báo, blog review, fanpage chính thức). TUYỆT ĐỐI không bịa URL.
- `url` phải trỏ thẳng tới file ảnh (jpg/png/webp), không phải trang HTML.
- MỞ XEM từng ảnh trước khi liệt kê: chỉ trả về ảnh ĐẠT — ảnh chụp thực tế
  (không phải logo, menu chữ, bản đồ, render 3D, chân dung cá nhân, watermark
  che kín) và nội dung là không gian / mặt tiền / đồ uống của quán.
- Xếp ảnh hợp làm ảnh bìa nhất lên đầu danh sách.
- Tiện thể tra luôn trên listing Google Maps của quán (search tên quán):
  - TOẠ ĐỘ chính xác → "lat"/"lng" (số thập phân). Không chắc → null.
  - ĐIỂM đánh giá công khai → "rating_overall" (đúng số nguồn nêu, 0–5) +
    "rating_source" (vd "Google Maps (~850 đánh giá)"). Không thấy → null.
  - **Không tự chấm, không ước lượng — chỉ chép số có thật.**

Trả về đúng một JSON object, không giải thích gì thêm:
{{"image_candidates": [{{"url": "https://…", "page": "https://trang-nguon"}}], "lat": null, "lng": null, "rating_overall": null, "rating_source": null}}
Không tìm được ảnh chắc chắn đúng quán → giữ "image_candidates": [] (các field khác vẫn điền nếu tra được)"""


def search_cover_candidates(
    *,
    name: str,
    address: str,
    district: str,
    known: list[dict] | None = None,
) -> CoverSearch:
    """Dedicated agy session: hunt + vet photos of THIS cafe, plus pick up the
    cafe's coordinates and public Google Maps score when the listing states
    them (Maps listings beat Nominatim on Vietnamese alley addresses). Empty
    CoverSearch on agy failure."""
    known_block = ""
    if known:
        lines = "\n".join(f"  - {c['url']}" for c in known[:4])
        known_block = (
            f"\nỨng viên đã biết từ bước viết bài (kiểm tra lại, đạt thì được ưu tiên):\n{lines}\n"
        )
    prompt = _SEARCH_PROMPT.format(
        name=name, address=address, district=district, known_block=known_block
    )
    try:
        # Hunting + opening images makes agy browse several pages — needs more
        # room than the article session.
        result = run_agy(prompt, timeout_sec=480)
    except AgyError as exc:
        log.warning("agy cover search failed for %s: %s", name, exc)
        return CoverSearch()
    candidates = parse_image_candidates(result.parsed.get("image_candidates"))
    if not candidates:
        log.info("agy found no cover candidates for %s", name)
    return CoverSearch(
        candidates=candidates,
        coords=parse_coords(result.parsed),
        rating=parse_rating(result.parsed),
    )


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


def find_cover(candidates: list[dict], *, name: str) -> dict | None:
    """First (already agent-vetted) candidate that downloads and passes
    dimension checks is re-hosted. Returns {"url", "source_page"} or None."""
    for cand in candidates[:4]:
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
