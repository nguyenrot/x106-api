"""Re-render the risograph variants for photos already stored.

    uv run python manage.py regenerate_riso_images            # missing + stale
    uv run python manage.py regenerate_riso_images --all      # everything
    uv run python manage.py regenerate_riso_images --dry-run  # just report

This is the answer to "what if we change the ink pair later". The colour master
is kept for exactly this reason: every variant is disposable and reproducible
from it. Editing a constant in `apps.core.riso` changes `palette_key()`, which
makes every existing row stale, and this command re-renders them.

Two things it deliberately does NOT do:

* delete superseded variants. The palette key is part of the derived filename,
  so old files simply stop being referenced. Leaving them costs a few hundred KB
  in the image repo and means a half-finished run never leaves a review pointing
  at a URL that 404s.
* touch `CafeReview`. Reviews reference the colour master; only the lookup table
  moves.
"""

from __future__ import annotations

import httpx
from django.core.management.base import BaseCommand

from apps.cafe.imaging import render_and_store
from apps.cafe.models import CafeImage, CafeReview
from apps.core.riso import palette_key

DOWNLOAD_TIMEOUT = 30.0
DOWNLOAD_UA = "cafe.kynguyen.cc riso-regenerate/1.0"


class Command(BaseCommand):
    help = "Render lại ảnh duotone risograph cho ảnh đã lưu (đổi màu mực thì chạy cái này)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Render lại tất cả, kể cả ảnh đã đúng bảng màu hiện tại.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chỉ liệt kê ảnh sẽ xử lý, không render và không ghi gì.",
        )
        parser.add_argument("--limit", type=int, default=0, help="Chỉ xử lý N ảnh đầu tiên.")

    def handle(self, *args, **opts):
        current = palette_key()
        self.stdout.write(f"palette_key hiện tại: {current}")

        known = {row.source_url: row for row in CafeImage.objects.all()}
        targets = self._targets(known, rerender_all=opts["all"])

        if opts["limit"]:
            targets = targets[: opts["limit"]]

        if not targets:
            self.stdout.write(self.style.SUCCESS("Không có ảnh nào cần render lại."))
            return

        self.stdout.write(f"{len(targets)} ảnh cần xử lý.")
        if opts["dry_run"]:
            for url in targets:
                row = known.get(url)
                state = "chưa có" if row is None else f"cũ ({row.palette_key or '—'})"
                self.stdout.write(f"  {state:>16}  {url}")
            return

        ok = failed = 0
        for i, url in enumerate(targets, 1):
            row = known.get(url)
            raw = self._download(url)
            if raw is None:
                failed += 1
                continue
            # An old row may predate `source_path`; fall back to the URL's tail so
            # the derivative still lands next to its master in the image repo.
            path = (row.source_path if row else "") or _path_from_url(url)
            result = render_and_store(raw, source_url=url, source_path=path)
            if result is None:
                failed += 1
                self.stdout.write(self.style.WARNING(f"  [{i}/{len(targets)}] lỗi render: {url}"))
                continue
            ok += 1
            self.stdout.write(f"  [{i}/{len(targets)}] {result.width}×{result.height}  {url}")

        style = self.style.SUCCESS if not failed else self.style.WARNING
        self.stdout.write(style(f"Xong: {ok} thành công, {failed} lỗi."))

    def _targets(self, known: dict[str, CafeImage], *, rerender_all: bool) -> list[str]:
        """Every referenced photo that has no variant, or a variant from an old palette.

        Sourced from the reviews rather than from `CafeImage` alone so photos
        uploaded before the pipeline existed get picked up as well.
        """
        current = palette_key()
        referenced: list[str] = []
        seen: set[str] = set()
        rows = CafeReview.objects.values_list("cover_image_url", "gallery")
        for cover, gallery in rows:
            for url in (cover, *(gallery or [])):
                if url and url not in seen:
                    seen.add(url)
                    referenced.append(url)
        for url in known:
            if url not in seen:
                seen.add(url)
                referenced.append(url)

        if rerender_all:
            return referenced
        return [
            url
            for url in referenced
            if url not in known or known[url].palette_key != current or not known[url].riso_url
        ]

    def _download(self, url: str) -> bytes | None:
        try:
            with httpx.Client(
                timeout=DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": DOWNLOAD_UA},
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.content
        except Exception as exc:  # noqa: BLE001 — one dead URL must not stop the run
            self.stdout.write(self.style.WARNING(f"  tải hỏng: {url} ({exc})"))
            return None


def _path_from_url(url: str) -> str:
    """`…/cafe/2026/08/abc.webp` → `cafe/2026/08/abc.webp`."""
    tail = url.split("://", 1)[-1]
    _, _, path = tail.partition("/")
    marker = "/cafe/"
    if marker in f"/{path}":
        idx = f"/{path}".index(marker)
        return f"/{path}"[idx + 1 :]
    return path or "cafe/unknown.webp"
