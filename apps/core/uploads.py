"""Image upload optimization — downscale + re-encode to WebP, strip metadata.

Public images (cafe covers, in-body shots, gallery photos) are pushed to a
public GitHub repo and served via the jsDelivr CDN when `CAFE_IMAGE_GITHUB_TOKEN`
is configured; otherwise they fall back to Django's local `default_storage`.
This keeps image bytes (and their bandwidth) off the VPS disk.

Ported from the lattice backend (apps/core/uploads.py) — same shape, x106 env
prefix. Reusable by any future app that needs public image hosting.
"""

from __future__ import annotations

import base64
import io
import logging
import uuid

import httpx
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

log = logging.getLogger("x106")

# Largest dimension we keep for a general content image. Callers may pass a cap.
DEFAULT_MAX_DIM = 1600
WEBP_QUALITY = 82

_GITHUB_API = "https://api.github.com"
_GITHUB_TIMEOUT = 20.0


class NotAnImage(Exception):
    pass


def absolute_https_url(request, url: str) -> str:
    """Absolute URL for a media path, forced to https outside DEBUG.

    Behind Cloudflare Flexible SSL the origin is reached over HTTP, so
    `build_absolute_uri` yields http:// — which the https frontend blocks as
    mixed content. Already-absolute URLs (jsDelivr CDN) pass through untouched.
    """
    if url.startswith(("http://", "https://")):
        return url

    from django.conf import settings

    full = request.build_absolute_uri(url)
    if not settings.DEBUG and full.startswith("http://"):
        full = "https://" + full[len("http://") :]
    return full


def github_image_enabled() -> bool:
    """True when public images should be pushed to GitHub + served via jsDelivr."""
    from django.conf import settings

    return bool(
        getattr(settings, "CAFE_IMAGE_GITHUB_TOKEN", "")
        and getattr(settings, "CAFE_IMAGE_GITHUB_REPO", "")
    )


def push_to_github(path: str, content: bytes, *, message: str | None = None) -> str:
    """Create `path` in the image repo via the GitHub Contents API; return its
    jsDelivr CDN URL. Filenames are uuid-unique so this is always a create.

    Raises on transport / HTTP error so the caller can fall back to local storage.
    """
    from django.conf import settings

    repo = settings.CAFE_IMAGE_GITHUB_REPO  # "owner/name"
    branch = getattr(settings, "CAFE_IMAGE_GITHUB_BRANCH", "main")
    token = settings.CAFE_IMAGE_GITHUB_TOKEN

    url = f"{_GITHUB_API}/repos/{repo}/contents/{path}"
    payload = {
        "message": message or f"add {path}",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cafe-image/1.0",
    }
    with httpx.Client(timeout=_GITHUB_TIMEOUT) as c:
        r = c.put(url, json=payload, headers=headers)
        r.raise_for_status()

    return f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}"


def optimize_image(raw: bytes, *, max_dim: int = DEFAULT_MAX_DIM) -> tuple[bytes, int, int]:
    """Return (webp_bytes, width, height). Honors EXIF orientation, downscales
    to fit `max_dim`, strips metadata, re-encodes to WebP."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise NotAnImage(str(exc)) from exc

    img = ImageOps.exif_transpose(img)  # bake in rotation, drop EXIF

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")

    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
    return out.getvalue(), img.width, img.height


def store_image(raw: bytes, *, prefix: str = "cafe", max_dim: int = DEFAULT_MAX_DIM) -> dict:
    """Optimize + persist an uploaded image. Returns metadata incl. the URL.

    The path is uuid-unguessable and lives under YYYY/MM so the media tree stays
    browsable. GitHub+jsDelivr when configured, else local default_storage.
    """
    webp, width, height = optimize_image(raw, max_dim=max_dim)
    from django.utils.timezone import now

    path = f"{prefix}/{now():%Y/%m}/{uuid.uuid4().hex}.webp"

    if github_image_enabled():
        try:
            cdn_url = push_to_github(path, webp)
            return {"path": path, "url": cdn_url, "width": width, "height": height, "byte_size": len(webp)}
        except Exception as exc:  # noqa: BLE001 — never lose an upload over a CDN hiccup
            log.warning("github image push failed (%s); falling back to local storage: %s", path, exc)

    saved_path = default_storage.save(path, ContentFile(webp))
    return {
        "path": saved_path,
        "url": default_storage.url(saved_path),
        "width": width,
        "height": height,
        "byte_size": len(webp),
    }
