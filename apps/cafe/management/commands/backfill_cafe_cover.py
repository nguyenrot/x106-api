"""Backfill a cover photo for an existing agent review.

    uv run python manage.py backfill_cafe_cover trinh-ca-phe
    uv run python manage.py backfill_cafe_cover brewman-coffee-concept --force

Asks agy (web search) for direct image URLs of THIS cafe, then runs the same
verify pipeline new posts use (download → dimension check → Gemini vision →
CDN re-host). `--force` overwrites an existing cover.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.cafe.agent.agy import AgyError, run_agy
from apps.cafe.agent.images import find_cover
from apps.cafe.agent.validate import parse_image_candidates
from apps.cafe.models import CafeReview

_PROMPT = """Bạn là trợ lý tìm ảnh cho blog cafe.kynguyen.cc. Dùng web search.

Nhiệm vụ: tìm 2–4 URL ảnh chụp THẬT của ĐÚNG quán cà phê sau ở Đà Nẵng:

- Tên quán: {name}
- Địa chỉ: {address}
- Khu vực: {district}

Quy tắc:
- Chỉ lấy ảnh bạn thực sự thấy trên trang công khai viết về đúng quán này
  (bài báo, blog review, fanpage chính thức). TUYỆT ĐỐI không bịa URL.
- `url` phải trỏ thẳng tới file ảnh (jpg/png/webp), không phải trang HTML.
- Ưu tiên ảnh không gian / mặt tiền / đồ uống; tránh logo, menu chữ, chân dung.

Trả về đúng một JSON object, không giải thích gì thêm:
{{"image_candidates": [{{"url": "https://…", "page": "https://trang-nguon"}}]}}
Không tìm được ảnh chắc chắn đúng quán → {{"image_candidates": []}}"""


class Command(BaseCommand):
    help = "Tìm + verify + gắn ảnh bìa cho một bài review đã có (theo slug)."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--force", action="store_true", help="Ghi đè cover đang có.")

    def handle(self, *args, **opts):
        try:
            review = CafeReview.objects.get(slug=opts["slug"])
        except CafeReview.DoesNotExist as e:
            raise CommandError(f"Không có bài với slug {opts['slug']!r}") from e

        if review.cover_image_url and not opts["force"]:
            raise CommandError("Bài đã có cover — dùng --force để ghi đè.")

        prompt = _PROMPT.format(name=review.name, address=review.address, district=review.district)
        try:
            result = run_agy(prompt, timeout_sec=240)
        except AgyError as e:
            raise CommandError(f"agy lỗi: {e}") from e

        candidates = parse_image_candidates(result.parsed.get("image_candidates"))
        self.stdout.write(f"agy trả {len(candidates)} ứng viên:")
        self.stdout.write(json.dumps(candidates, ensure_ascii=False, indent=2))
        if not candidates:
            self.stdout.write(self.style.WARNING("Không có ứng viên — giữ nguyên không cover."))
            return

        cover = find_cover(
            candidates, name=review.name, district=review.district, excerpt=review.excerpt
        )
        if not cover:
            self.stdout.write(self.style.WARNING("Không ứng viên nào qua được verify — không gắn."))
            return

        review.cover_image_url = cover["url"]
        review.save(update_fields=["cover_image_url", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Đã gắn cover: {cover['url']}"))
        if cover.get("source_page"):
            self.stdout.write(f"nguồn ảnh: {cover['source_page']}")
