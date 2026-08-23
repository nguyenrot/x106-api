"""Risograph duotone pipeline — grayscale → autocontrast → colorize → halftone.

Pure imaging code: no Django models, no storage, no network. `apps.cafe.imaging`
is the orchestration layer that persists what this module renders.

Why it runs here and not in the browser
---------------------------------------
Every cafe photo has to land on the SAME two-ink ramp, and the step that makes
that true is `autocontrast` — a shot taken under yellow bulbs at 21:00 and one
taken on a bright terrace only normalize to the same tonal range if you stretch
each one's own histogram first. CSS filters and `mix-blend-mode` cannot do a
per-image histogram stretch, so a client-side duotone leaves the dim shots muddy
and the bright ones blown out. Rendering once at upload time also means the
browser downloads a single flat WebP instead of a full-colour photo plus a blend
stack it has to composite on every paint.

The ink model
-------------
A risograph lays one flat spot colour per drum. Two drums (orange + green) give
three readable values: bare paper where no ink lands, a single ink where one
drum prints, and the near-black `OVERPRINT` where both cover the same spot.
`ImageOps.colorize(black=OVERPRINT, mid=RISO_GREEN, white=PAPER)` maps luminance
straight onto that ramp — shadows get both inks, midtones the green drum alone,
highlights stay unprinted. Photos only ever use the green drum; the orange one
is reserved for UI and decoration so the two never fight for attention.

The halftone
------------
Real riso has no continuous tone: it screens the image into dots. We rebuild
that with an analytic 45° clustered-dot screen (see `_screen_tile`) blended in
across the midtones only — dots in the deepest shadows clog into a solid patch
("bết"), and dots in the brightest highlights speckle an area that should read
as clean paper. Both constants below were tuned against real cafe covers: the
screen has to survive being downscaled into a ~560 px card without turning the
photo into newsprint.

Changing any constant here changes `palette_key()`, which is what
`manage.py regenerate_riso_images` compares against to find stale variants.
"""

from __future__ import annotations

import hashlib
import io
import math
from functools import lru_cache

from PIL import Image, ImageChops, ImageOps

# ── Spot colours ────────────────────────────────────────────────────────────
# Must stay in lockstep with the CSS tokens in cafe/app/assets/css/riso.css.
RISO_ORANGE = (0xFF, 0x6C, 0x2F)  # --riso-orange — UI/decoration drum only
RISO_GREEN = (0x3D, 0x6B, 0x52)   # --riso-green  — the photographic drum
OVERPRINT = (0x2E, 0x24, 0x16)    # --overprint   — both drums, i.e. shadows
PAPER = (0xF5, 0xEF, 0xE3)        # --paper       — no ink

# ── Tone normalisation ──────────────────────────────────────────────────────
# Clip 2% off each end of the histogram before stretching: low enough to keep a
# genuinely low-key photo low-key, high enough to kill the haze a phone's auto
# exposure leaves in a dim cafe.
AUTOCONTRAST_CUTOFF = 2

# Straight autocontrast → colorize prints far too much ink: luminance 128 lands
# on RISO_GREEN, which is itself dark (~L95), so every midtone gets pushed a
# stop down and the picture reads as muddy olive. Lifting the midtones with a
# gamma and pulling the ramp's midpoint below centre puts the green where a
# real duplicator puts it — carrying the midtones, not the shadows.
MIDTONE_GAMMA = 1.30
COLORIZE_MIDPOINT = 112

# ── Halftone screen ─────────────────────────────────────────────────────────
HALFTONE_LPI = 90                 # screen ruling, lines per inch
HALFTONE_RENDER_DPI = 720         # resolution the 1x variant is treated as printing at
HALFTONE_ANGLE_DEG = 45           # baked into `_screen_tile`'s closed form
HALFTONE_STRENGTH = 0.30          # max blend toward the screened copy, in midtones
HALFTONE_EDGE = 10                # dot-edge softness in luminance units (ink spread)

# Midtone window over the 0–255 luminance axis. The screen fades in across
# [LO, CORE_LO] and back out across [CORE_HI, HI]; it is absent outside.
MID_LO, MID_CORE_LO = 48, 100
MID_CORE_HI, MID_HI = 170, 220

# ── Output ──────────────────────────────────────────────────────────────────
# Quality 72 rather than the 82 the colour originals use: the dot screen is
# synthetic high-frequency detail that WebP spends a lot of bits on, and at 1:1
# a q72 screened photo is indistinguishable from q82 — the dots hide the
# artefacts. Worth ~25% off every variant.
WEBP_QUALITY = 72
RISO_MAX_DIM_1X = 800             # longest edge of the 1x variant
RISO_MAX_DIM_2X = 1600            # …and of the retina one (never upscales)

# Bump when the algorithm changes in a way that should invalidate stored
# variants even though none of the constants above moved.
PIPELINE_VERSION = 1


def palette_key() -> str:
    """Short digest of every input that affects the rendered output.

    Stored on each `CafeImage` row so `regenerate_riso_images` can find variants
    rendered with a superseded ink pair, tone curve or screen.
    """
    parts = (
        PIPELINE_VERSION,
        RISO_GREEN, OVERPRINT, PAPER,
        AUTOCONTRAST_CUTOFF, MIDTONE_GAMMA, COLORIZE_MIDPOINT,
        HALFTONE_LPI, HALFTONE_RENDER_DPI, HALFTONE_ANGLE_DEG,
        HALFTONE_STRENGTH, HALFTONE_EDGE,
        MID_LO, MID_CORE_LO, MID_CORE_HI, MID_HI,
        WEBP_QUALITY, RISO_MAX_DIM_1X, RISO_MAX_DIM_2X,
    )
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:12]


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


# ── Screen construction ─────────────────────────────────────────────────────

def screen_period() -> int:
    """Pixel period of the 45° screen tile at 1x.

    A dot screen at angle θ and ruling `f` (cycles per pixel) is the product of
    two cosine gratings, one along each screen axis. At exactly 45° the axes are
    u=(x+y)/√2 and v=(y−x)/√2, and the product collapses through
    cos(A)cos(B) = ½[cos(A−B) + cos(A+B)] into

        s(x, y) ∝ cos(2πx/P) + cos(2πy/P),   P = DPI / (√2 · LPI)

    …which is separable, exactly periodic on the integer pixel grid, and needs
    no rotation pass. That matters: rotating a tiled bitmap 45° resamples it,
    and at these pitches the interpolation smears the dot edges into mush.
    """
    return max(2, round(HALFTONE_RENDER_DPI / (math.sqrt(2) * HALFTONE_LPI)))


@lru_cache(maxsize=8)
def _screen_tile(period: int) -> Image.Image:
    """One period×period tile of the 45° clustered-dot threshold field.

    Maxima — where ink is laid down last, i.e. only in the darkest areas — sit
    at (0,0) and (P/2,P/2): a square lattice turned 45° to the pixel grid, with
    nearest-neighbour spacing P/√2.
    """
    cos_axis = [(math.cos(2 * math.pi * i / period) + 1) / 2 for i in range(period)]
    buf = bytearray(period * period)
    for y in range(period):
        cy = cos_axis[y]
        row = y * period
        for x in range(period):
            buf[row + x] = round(255 * (cos_axis[x] + cy) / 2)
    return Image.frombytes("L", (period, period), bytes(buf))


@lru_cache(maxsize=4)
def _screen_canvas(period: int, width: int, height: int) -> Image.Image:
    """The tile repeated to fill `width`×`height`.

    Cached on rounded-up dimensions by `_screen_for`, so a run of similar photos
    reuses one canvas instead of re-tiling per image. `paste` clips at the
    edges, so the loops are free to overrun by up to one tile.
    """
    tile = _screen_tile(period)
    tile_w, tile_h = tile.size

    row = Image.new("L", (width, tile_h))
    for x in range(0, width, tile_w):
        row.paste(tile, (x, 0))

    canvas = Image.new("L", (width, height))
    for y in range(0, height, tile_h):
        canvas.paste(row, (0, y))
    return canvas


def _screen_for(size: tuple[int, int], period: int) -> Image.Image:
    """Screen field cropped to exactly `size`, hitting the rounded cache."""
    width, height = size
    grid = 256
    return _screen_canvas(
        period,
        max(grid, -(-width // grid) * grid),
        max(grid, -(-height // grid) * grid),
    ).crop((0, 0, width, height))


@lru_cache(maxsize=4)
def _dot_lut(edge: int) -> tuple[int, ...]:
    """Signed (luminance − threshold) + 128 → ink coverage, with a soft edge.

    A hard `L > screen` test gives mathematically perfect dots and physically
    wrong ones: real ink spreads a little where it meets paper. The ramp also
    keeps the dot edges from aliasing when the browser downscales the asset into
    a card, and costs nothing — it is the same single `point()` call.
    """
    return tuple(round(255 * _smoothstep(128 - edge, 128 + edge, v)) for v in range(256))


@lru_cache(maxsize=2)
def _midtone_lut(strength_pct: int) -> tuple[int, ...]:
    """Luminance → screen opacity (0–255), zero outside the midtone window.

    Keyed on an integer percentage because `lru_cache` needs a hashable key and
    a float strength would defeat it in practice.
    """
    strength = strength_pct / 100
    return tuple(
        round(
            255
            * strength
            * _smoothstep(MID_LO, MID_CORE_LO, v)
            * (1 - _smoothstep(MID_CORE_HI, MID_HI, v))
        )
        for v in range(256)
    )


# ── Pipeline stages ─────────────────────────────────────────────────────────

def _normalize(gray: Image.Image, *, max_dim: int) -> Image.Image:
    """Steps 1–2 (+ tone curve): downscale, stretch the histogram, lift midtones.

    Autocontrast runs *after* the resize so it measures the histogram of the
    pixels that actually ship, not of detail that gets averaged away.
    """
    out = gray.copy()
    if max(out.size) > max_dim:
        out.thumbnail((max_dim, max_dim), Image.LANCZOS)
    out = ImageOps.autocontrast(out, cutoff=AUTOCONTRAST_CUTOFF)
    return out.point([round(255 * (v / 255) ** (1 / MIDTONE_GAMMA)) for v in range(256)])


def _colorize(luma: Image.Image) -> Image.Image:
    """Step 3: luminance → the two-ink ramp (overprint → green → paper)."""
    return ImageOps.colorize(
        luma, black=OVERPRINT, mid=RISO_GREEN, white=PAPER, midpoint=COLORIZE_MIDPOINT
    )


def _screened(luma: Image.Image, *, period: int) -> Image.Image:
    """Steps 3–4: colorize, then blend the dot screen back in over the midtones.

    The screened copy is built by comparing luminance against the dot field and
    running the result through the *same* colorize ramp, so a dot reads as real
    ink rather than as a flat grey stencil laid over the picture.
    `Image.composite` then cross-fades the two using the midtone window as alpha.
    """
    threshold = ImageChops.subtract(luma, _screen_for(luma.size, period), scale=1, offset=128)
    dots = threshold.point(_dot_lut(HALFTONE_EDGE))
    mask = luma.point(_midtone_lut(round(HALFTONE_STRENGTH * 100)))
    return Image.composite(_colorize(dots), _colorize(luma), mask)


def _encode(img: Image.Image) -> tuple[bytes, int, int]:
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=6)
    return buf.getvalue(), img.width, img.height


def _to_gray(raw: bytes) -> Image.Image:
    """Decode once, bake in EXIF rotation, drop to a single channel."""
    with Image.open(io.BytesIO(raw)) as src:
        src.load()
        return ImageOps.exif_transpose(src).convert("L")


# ── Public entry points ─────────────────────────────────────────────────────

def render_duotone(raw: bytes, *, max_dim: int, period: int | None = None) -> tuple[bytes, int, int]:
    """Full pipeline over one source image. Returns (webp_bytes, width, height)."""
    luma = _normalize(_to_gray(raw), max_dim=max_dim)
    return _encode(_screened(luma, period=period or screen_period()))


def render_variants(raw: bytes) -> dict[str, tuple[bytes, int, int]]:
    """Both display variants of one photo, keyed `"1x"` / `"2x"`.

    The 2x screen period is scaled by the two variants' *actual* width ratio
    rather than a hardcoded 2. When the source is smaller than `RISO_MAX_DIM_2X`
    the two variants come out at similar sizes, and a hardcoded doubling would
    print visibly coarser dots on the retina asset than on the one it replaces.
    Neither variant upscales — `thumbnail` is a no-op below the cap.
    """
    gray = _to_gray(raw)
    one = _normalize(gray, max_dim=RISO_MAX_DIM_1X)
    two = _normalize(gray, max_dim=RISO_MAX_DIM_2X)

    period_1x = screen_period()
    period_2x = max(period_1x, round(period_1x * two.width / one.width))

    return {
        "1x": _encode(_screened(one, period=period_1x)),
        "2x": _encode(_screened(two, period=period_2x)),
    }
