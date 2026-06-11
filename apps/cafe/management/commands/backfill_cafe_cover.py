"""Backfill a cover photo for an existing agent review.

    uv run python manage.py backfill_cafe_cover trinh-ca-phe
    uv run python manage.py backfill_cafe_cover brewman-coffee-concept --force

Same pipeline new posts use: a dedicated agy session hunts + vets photos of
THIS cafe, then download → dimension check → CDN re-host. `--force` overwrites
an existing cover.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.cafe.agent.images import find_cover, search_cover_candidates
from apps.cafe.models import CafeReview


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

        need_cover = not review.cover_image_url or opts["force"]
        need_coords = review.lat is None
        need_rating = review.rating_overall is None
        if not need_cover and not need_coords and not need_rating:
            raise CommandError("Bài đã đủ cover, toạ độ lẫn điểm — dùng --force để ghi đè cover.")

        search = search_cover_candidates(
            name=review.name, address=review.address, district=review.district
        )
        candidates = search.candidates
        self.stdout.write(
            f"agy trả {len(candidates)} ứng viên ảnh, coords: {search.coords}, rating: {search.rating}"
        )
        self.stdout.write(json.dumps(candidates, ensure_ascii=False, indent=2))

        update_fields = ["updated_at"]
        if need_coords and search.coords:
            review.lat, review.lng = search.coords
            update_fields += ["lat", "lng"]
            self.stdout.write(self.style.SUCCESS(f"Đã gắn toạ độ: {search.coords}"))

        if need_rating and search.rating:
            review.rating_overall = search.rating[0]
            update_fields.append("rating_overall")
            self.stdout.write(self.style.SUCCESS(f"Đã gắn điểm: {search.rating[0]} ({search.rating[1]})"))

        if need_cover and candidates:
            cover = find_cover(candidates, name=review.name)
            if cover:
                review.cover_image_url = cover["url"]
                update_fields.append("cover_image_url")
                self.stdout.write(self.style.SUCCESS(f"Đã gắn cover: {cover['url']}"))
                if cover.get("source_page"):
                    self.stdout.write(f"nguồn ảnh: {cover['source_page']}")
            else:
                self.stdout.write(self.style.WARNING("Không ứng viên ảnh nào tải được/đạt kích thước."))
        elif need_cover:
            self.stdout.write(self.style.WARNING("Không có ứng viên ảnh."))

        if len(update_fields) > 1:
            review.save(update_fields=update_fields)
        else:
            self.stdout.write(self.style.WARNING("Không có gì để cập nhật."))
