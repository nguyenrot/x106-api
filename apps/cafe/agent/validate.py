"""Validate the agent's JSON into a CafeReviewWriteSerializer-ready payload.

Mirrors lattice's apps/agent/validate.py shape: `SkipSignal` for deliberate
agent skips, `ValidationError` for malformed/dishonest output. Both trigger the
one-shot nudge retry in the runner.

The tag/amenity/district catalogs mirror the frontend's app/lib/cafe.ts — keep
them in sync when the frontend catalogues change.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.text import slugify_vi

# Khu vực quen miệng của dân Đà Nẵng — the blog's `district` facet values.
DISTRICTS = [
    "Hải Châu",
    "Thanh Khê",
    "Sơn Trà",
    "Ngũ Hành Sơn",
    "Liên Chiểu",
    "Cẩm Lệ",
    "Hòa Vang",
]

# slug → human label (frontend TAG_SUGGESTIONS + the agent's disclosure tag).
TAG_CATALOG = {
    "specialty": "cà phê specialty",
    "view-bien": "view biển",
    "view-song": "view sông",
    "co-dien": "cổ điển",
    "hien-dai": "hiện đại",
    "vintage": "vintage",
    "san-vuon": "sân vườn",
    "rooftop": "rooftop",
    "workspace": "hợp làm việc",
    "check-in": "đẹp để check-in",
    "gia-re": "giá rẻ",
    "sang-trong": "sang trọng",
}
AGENT_TAG = "tong-hop"  # auto-appended; marks agent-written posts on the site

# slug → label (frontend AMENITIES catalogue).
AMENITY_CATALOG = {
    "wifi": "wifi mạnh",
    "quiet": "yên tĩnh",
    "work": "hợp làm việc",
    "parking": "chỗ đậu xe",
    "outdoor": "sân ngoài trời",
    "ac": "máy lạnh",
    "view": "view đẹp",
    "food": "có đồ ăn",
    "pet": "cho thú cưng",
    "late": "mở khuya",
}


class ValidationError(Exception):
    pass


class SkipSignal(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ValidatedReview:
    """Serializer-ready payload + the bits the runner logs/geocodes."""

    payload: dict
    name: str
    address: str
    sources: list[str]
    confidence: float


def _word_count(text: str) -> int:
    return len(text.split())


def _norm_district(raw: str) -> str | None:
    """Accept 'Quận Hải Châu' / 'hải châu' / 'Hai Chau' → 'Hải Châu'."""
    cleaned = (raw or "").strip()
    for prefix in ("Quận ", "quận ", "Huyện ", "huyện "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    key = slugify_vi(cleaned)
    for d in DISTRICTS:
        if slugify_vi(d) == key:
            return d
    return None


def validate(
    parsed: dict,
    *,
    existing_slugs: set[str],
    min_confidence: float = 0.7,
) -> ValidatedReview:
    if not isinstance(parsed, dict):
        raise ValidationError("output không phải JSON object")

    action = parsed.get("action")
    if action == "skip":
        raise SkipSignal(str(parsed.get("reason") or "agent skipped"))
    if action != "publish":
        raise ValidationError(f"action không hợp lệ: {action!r}")

    name = (parsed.get("name") or "").strip()
    if not 2 <= len(name) <= 200:
        raise ValidationError("name thiếu hoặc quá dài")
    slug = slugify_vi(name)
    if slug in existing_slugs:
        raise ValidationError(f"quán '{name}' đã có bài (slug {slug}) — chọn quán khác")

    district = _norm_district(parsed.get("district") or "")
    if district is None:
        raise ValidationError(f"district không nằm trong danh sách: {parsed.get('district')!r}")

    address = (parsed.get("address") or "").strip()
    if len(address) < 8:
        raise ValidationError("address thiếu hoặc quá ngắn")

    excerpt = (parsed.get("excerpt") or "").strip()
    if not 30 <= len(excerpt) <= 300:
        raise ValidationError(f"excerpt phải 30–300 ký tự (đang {len(excerpt)})")

    content_md = (parsed.get("content_md") or "").strip()
    words = _word_count(content_md)
    if not 350 <= words <= 1300:
        raise ValidationError(f"content_md phải ~500–900 từ (đang {words})")
    if "tổng hợp" not in content_md.lower():
        raise ValidationError("content_md thiếu câu khép bài 'tổng hợp từ các nguồn công khai'")

    sources = [
        s.strip()
        for s in (parsed.get("sources") or [])
        if isinstance(s, str) and s.strip().startswith(("http://", "https://"))
    ]
    if not sources:
        raise ValidationError("sources rỗng — bài tổng hợp bắt buộc có nguồn")

    try:
        confidence = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence:
        raise SkipSignal(f"confidence {confidence:.2f} < {min_confidence}")

    # Optional facts — null when the sources didn't state them.
    price_level = parsed.get("price_level")
    if price_level is not None:
        try:
            price_level = int(price_level)
        except (TypeError, ValueError):
            price_level = None
        else:
            if not 1 <= price_level <= 4:
                price_level = None

    rating_overall = parsed.get("rating_overall")
    rating_source = (parsed.get("rating_source") or "").strip()
    if rating_overall is not None:
        try:
            rating_overall = round(float(rating_overall), 1)
        except (TypeError, ValueError):
            rating_overall = None
        else:
            if not 0 <= rating_overall <= 5 or not rating_source:
                rating_overall = None  # no source → no score (anti-fabrication)

    tags = [t for t in (parsed.get("tags") or []) if isinstance(t, str) and t in TAG_CATALOG]
    tags = list(dict.fromkeys([*tags, AGENT_TAG]))
    amenities = [
        a for a in (parsed.get("amenities") or []) if isinstance(a, str) and a in AMENITY_CATALOG
    ]

    payload = {
        "name": name,
        "excerpt": excerpt[:300],
        "address": address[:300],
        "district": district,
        "price_level": price_level,
        "price_note": (parsed.get("price_note") or "").strip()[:120],
        "opening_hours": (parsed.get("opening_hours") or "").strip()[:200],
        "amenities": amenities,
        "tags": tags,
        "rating_overall": rating_overall,
        "content_md": content_md,
        "is_published": True,
        "visited_at": None,  # giọng tổng hợp — chưa ai ghé, không bịa ngày
        "cover_image_url": "",
        "gallery": [],
    }
    return ValidatedReview(
        payload=payload,
        name=name,
        address=address,
        sources=sources,
        confidence=confidence,
    )
