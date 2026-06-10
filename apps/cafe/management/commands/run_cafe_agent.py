"""Manual trigger for the cafe review agent — VPS shell testing.

    uv run python manage.py run_cafe_agent --dry-run --force   # agy + validate + geocode, no DB write
    uv run python manage.py run_cafe_agent --force             # publish one review now
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from apps.cafe.agent.runner import run_cafe_agent


class Command(BaseCommand):
    help = "Chạy agent tổng hợp bài review quán cà phê (1 bài/lần chạy)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Gọi agy + validate nhưng không ghi DB.")
        parser.add_argument("--force", action="store_true", help="Bỏ qua gate CAFE_AGENT_ENABLED.")
        parser.add_argument("--slot", default="manual", help="Nhãn slot ghi vào run (daily|manual).")

    def handle(self, *args, **opts):
        result = run_cafe_agent(opts["slot"], dry_run=opts["dry_run"], force=opts["force"])

        self.stdout.write(f"status: {result.status}")
        if result.reason:
            self.stdout.write(f"reason: {result.reason}")
        if result.payload is not None:
            self.stdout.write("payload:")
            self.stdout.write(json.dumps(result.payload.payload, ensure_ascii=False, indent=2, default=str))
            self.stdout.write(f"sources: {result.payload.sources}")
            self.stdout.write(f"confidence: {result.payload.confidence}")
        if result.review is not None:
            self.stdout.write(self.style.SUCCESS(f"published: {result.review.slug} ({result.review.name})"))
