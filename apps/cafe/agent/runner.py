"""Core orchestration shared by the Celery tasks and the management command.

One run = gather dedup context → build prompt → call agy (up to 2 attempts) →
validate → geocode via Nominatim → create a published CafeReview through the
existing write serializer → record a CafeAgentRun.

`dry_run=True` still calls agy + geocode but writes nothing — the management
command prints the validated payload instead.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.utils import timezone

from apps.cafe.models import CafeAgentRun, CafeReview
from apps.cafe.serializers import CafeReviewWriteSerializer

from .agy import AgyError, run_agy
from .geocode import geocode_da_nang
from .images import find_cover
from .prompts import build_write_review_prompt
from .validate import SkipSignal, ValidatedReview, ValidationError, validate

log = logging.getLogger("apps.cafe.agent")


class RunResult:
    def __init__(
        self,
        status: str,
        *,
        review: CafeReview | None = None,
        payload: ValidatedReview | None = None,
        reason: str = "",
    ):
        self.status = status
        self.review = review
        self.payload = payload
        self.reason = reason


def _attempt(prompt: str, existing_slugs: set[str], min_conf: float):
    """Return (ValidatedReview|None, error_str|None, raw, parsed, agy_ms)."""
    try:
        result = run_agy(prompt)
    except AgyError as e:
        return None, f"agy error: {e}", getattr(e, "raw_output", "") or "", None, getattr(e, "duration_ms", 0) or 0

    try:
        review = validate(result.parsed, existing_slugs=existing_slugs, min_confidence=min_conf)
        return review, None, result.raw_output, result.parsed, result.duration_ms
    except SkipSignal as e:
        return None, f"skip: {e.reason}", result.raw_output, result.parsed, result.duration_ms
    except ValidationError as e:
        return None, f"validation: {e}", result.raw_output, result.parsed, result.duration_ms


def run_cafe_agent(
    slot: str = "manual",
    *,
    dry_run: bool = False,
    force: bool = False,
    run: CafeAgentRun | None = None,
) -> RunResult:
    """Research + write + publish one cafe review.

    `force=True` bypasses the CAFE_AGENT_ENABLED gate (admin "Tạo bài AI"
    button). `run` lets the manual endpoint pass its pre-created row so it can
    return the id before the slow work happens.
    """
    started = time.monotonic()

    if run is None and not dry_run:
        run = CafeAgentRun.objects.create(
            slot=slot if slot in dict(CafeAgentRun.SLOT_CHOICES) else "manual",
            status="started",
        )

    if not getattr(settings, "CAFE_AGENT_ENABLED", False) and not dry_run and not force:
        _finish(run, "skipped", error="CAFE_AGENT_ENABLED is false", started=started)
        log.info("cafe agent disabled (CAFE_AGENT_ENABLED=false); skipping")
        return RunResult("skipped", reason="disabled")

    existing = list(CafeReview.objects.values_list("name", "slug"))
    existing_slugs = {slug for _, slug in existing}
    min_conf = getattr(settings, "CAFE_AGENT_MIN_CONFIDENCE", 0.7)
    prompt = build_write_review_prompt(
        today_vn=timezone.localdate().isoformat(),
        existing=existing,
    )

    review, err, raw, parsed, agy_ms = _attempt(prompt, existing_slugs, min_conf)

    # One nudge retry on ANY first failure — transient agy faults included.
    if review is None:
        log.info("first attempt failed (%s); retrying with a nudge", err)
        prompt2 = prompt + (
            "\n\nLƯU Ý: lần thử trước chưa đạt. Hãy chọn một quán KHÁC (chưa có bài), "
            "kiểm tra nguồn kỹ hơn và đảm bảo trả về đúng MỘT JSON object hợp lệ.\n"
        )
        review2, err2, raw2, parsed2, agy_ms2 = _attempt(prompt2, existing_slugs, min_conf)
        agy_ms += agy_ms2
        if review2 is not None:
            review, err, raw, parsed = review2, None, raw2, parsed2
        else:
            err, raw, parsed = err2, raw2, parsed2

    if run is not None:
        run.prompt = prompt
        run.agy_response_raw = (raw or "")[:200_000]
        run.agy_response_parsed = parsed
        run.agy_duration_ms = agy_ms

    if review is None:
        if run is not None:
            run.validation_error = err or ""
        _finish(run, "skipped", error=err or "no valid review", started=started)
        log.info("cafe agent produced no valid review: %s", err)
        return RunResult("skipped", reason=err or "")

    if run is not None:
        run.cafe_name = review.name

    # Best-effort coordinates; a miss publishes without a pin (map falls back
    # to a text query on the frontend).
    coords = geocode_da_nang(review.address, review.name)
    if coords:
        review.payload["lat"] = round(coords[0], 6)
        review.payload["lng"] = round(coords[1], 6)
        log.info("geocoded %r → %s", review.name, coords)
    else:
        log.info("no geocode hit for %r — publishing without coordinates", review.name)

    if dry_run:
        return RunResult("dry-run", payload=review)

    # Best-effort cover: download → dimension check → Gemini verify → CDN.
    # Skipped in dry-run (store_image writes to the image repo). Never blocks.
    if review.image_candidates:
        try:
            cover = find_cover(
                review.image_candidates,
                name=review.name,
                district=review.payload["district"],
                excerpt=review.payload["excerpt"],
            )
            if cover:
                review.payload["cover_image_url"] = cover["url"]
        except Exception:
            log.exception("cover pipeline failed — publishing without cover")

    try:
        ser = CafeReviewWriteSerializer(data=review.payload)
        ser.is_valid(raise_exception=True)
        created = ser.save()
    except Exception as exc:
        log.exception("cafe agent publish failed")
        _finish(run, "failed", error=f"publish failed: {exc}", started=started)
        return RunResult("failed", reason=str(exc))

    if run is not None:
        run.review = created
    _finish(run, "succeeded", started=started)
    log.info("published cafe review %s (%s)", created.slug, created.name)
    return RunResult("succeeded", review=created, payload=review)


def _finish(run: CafeAgentRun | None, status: str, *, error: str = "", started: float) -> None:
    if run is None:
        return
    run.status = status
    if error:
        run.error_message = error[:2000]
    run.duration_ms = int((time.monotonic() - started) * 1000)
    run.ended_at = timezone.now()
    run.save()
