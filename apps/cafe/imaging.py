"""Storage + bookkeeping around the risograph pipeline.

`apps.core.riso` renders; `apps.core.uploads` persists; this module joins the
two and records the result so the variants can be looked up by the URL a review
already stores, and re-rendered when the ink pair changes.

Every entry point here is failure-tolerant by design: an upload must never be
lost because the duotone pass raised. A review with no variant simply falls back
to its colour master on the frontend.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from apps.core import riso
from apps.core.uploads import (
    DEFAULT_MAX_DIM,
    absolutize_media_url,
    store_bytes,
    store_image,
)

from .models import CafeImage

log = logging.getLogger("apps.cafe.imaging")

def _derived_path(master_path: str, *, palette: str, retina: bool) -> str:
    """Where a variant lives, beside its master.

    Two deliberate choices in this name:

    * the palette key is IN the filename, so re-rendering with a different ink
      pair produces a different URL. jsDelivr caches a `@main` path for days;
      reusing the URL would leave every reader looking at the old colours long
      after the regeneration finished.
    * "-2x", not "@2x" — the jsDelivr URL format already uses `@` as its
      ref separator.
    """
    base = master_path[: -len(".webp")] if master_path.endswith(".webp") else master_path
    return f"{base}.riso-{palette}{'-2x' if retina else ''}.webp"


def render_and_store(raw: bytes, *, source_url: str, source_path: str) -> CafeImage | None:
    """Render both duotone variants of `raw`, store them, upsert the index row.

    `raw` is the ORIGINAL upload bytes, not the optimized master: the pipeline
    does its own downscale, and running it on the already-recompressed WebP
    would stack a second generation of lossy artefacts under the halftone.

    Returns None (having logged) if anything fails — the caller keeps its master.
    """
    try:
        variants = riso.render_variants(raw)
    except Exception as exc:  # noqa: BLE001 — a bad decode must not fail the upload
        log.warning("riso render failed for %s: %s", source_url, exc)
        return None

    palette = riso.palette_key()
    try:
        one_bytes, width, height = variants["1x"]
        two_bytes, _, _ = variants["2x"]
        riso_url = store_bytes(one_bytes, _derived_path(source_path, palette=palette, retina=False))
        riso_2x_url = store_bytes(two_bytes, _derived_path(source_path, palette=palette, retina=True))
    except Exception as exc:  # noqa: BLE001
        log.warning("riso store failed for %s: %s", source_url, exc)
        return None

    row, _ = CafeImage.objects.update_or_create(
        source_url=source_url,
        defaults={
            "source_path": source_path,
            "riso_url": absolutize_media_url(riso_url),
            "riso_2x_url": absolutize_media_url(riso_2x_url),
            "width": width,
            "height": height,
            "palette_key": palette,
        },
    )
    return row


def store_review_image(raw: bytes, *, prefix: str = "cafe", max_dim: int = DEFAULT_MAX_DIM) -> dict:
    """Store an uploaded photo and its duotone variants in one call.

    Returns `store_image`'s metadata plus `riso_url` / `riso_2x_url` /
    `riso_width` / `riso_height` when the duotone pass succeeded. Every URL is
    absolute, so the caller can hand the payload straight to a client on another
    host.
    """
    meta = store_image(raw, prefix=prefix, max_dim=max_dim)
    meta["url"] = absolutize_media_url(meta["url"])

    row = render_and_store(raw, source_url=meta["url"], source_path=meta["path"])
    if row is not None:
        meta.update(
            riso_url=row.riso_url,
            riso_2x_url=row.riso_2x_url,
            riso_width=row.width,
            riso_height=row.height,
        )
    return meta


def riso_map(urls: Iterable[str]) -> dict[str, dict]:
    """`{source_url: {"src", "src2x", "w", "h"}}` for the URLs that have variants.

    One query for the whole batch. The public feed serializes every published
    review in a single response, so resolving these per row would be a textbook
    N+1 against a table that is only ever read by URL.
    """
    wanted = {u for u in urls if u}
    if not wanted:
        return {}
    rows = CafeImage.objects.filter(source_url__in=wanted).exclude(riso_url="")
    return {row.source_url: row.as_variant() for row in rows}
