"""LLMScene v3 validator/clamp. Ported from internal/service/llm.go.

The renderer (frontend) only handles the enums in these whitelists. Anything
out-of-vocabulary gets rewritten to a safe default; out-of-bbox positions are
clamped; arrays beyond the cap are truncated. Hard errors (bad palette, no
shapes) raise SceneValidationError so the worker can retry.

Also hosts validate_outline() — the LLMOutline schema for the hierarchical
random pipeline (stage 1). Permissive: coerces enums and clamps numbers,
raises only on missing palette / empty cluster list / nonsense totals."""

from __future__ import annotations

from apps.core.text import clamp_runes

from ..errors import OutlineValidationError, SceneValidationError

PALETTES = frozenset({
    "poster-bright", "museum-pop", "soft-electric",
    "forest-calm", "sunset-coral", "ocean-mist",
    "pastel-garden", "mono-bold", "tropical-punch",
    "vintage-press",
})
SHAPE_KINDS = frozenset({
    "sphere", "box", "torus", "knot",
    "panel", "cone", "cylinder", "capsule",
    "icosahedron", "octahedron", "disc",
    "tetrahedron", "dodecahedron", "ring", "prism", "pyramid",
})
MATERIALS = frozenset({
    "matte", "glass", "metal", "glow",
    "iridescent", "velvet", "wireframe",
})
MOTIONS = frozenset({
    "still", "float", "spin", "orbit", "pulse",
    "wobble", "swing", "drift",
})
FONTS = frozenset({"sans", "serif", "round", "square"})
TEXT_ALIGNS = frozenset({"left", "center", "right"})

LAYOUTS = frozenset({
    "dense-vortex", "dense-constellation", "dense-grid",
    "multi-ring", "layered-horizon", "wave-field",
    "dense-mandala", "cluster-negative",
})
REGIONS = frozenset({"top", "bottom", "left", "right", "center", "scattered"})
CLUSTER_ROLES = frozenset({"hero", "background", "accent"})

SCENE_VERSION = 3
TITLE_MAX_RUNES = 40
AINOTES_MAX_RUNES = 200
TEXT_MAX_RUNES = 120
SHAPE_MAX_COUNT = 100
SHAPE_MIN_COUNT = 1
TEXT_MAX_COUNT = 4

# Outline (stage 1) constants
OUTLINE_DENSITY_MIN = 30
OUTLINE_DENSITY_MAX = 100
OUTLINE_CLUSTER_MIN = 2
OUTLINE_CLUSTER_MAX = 8
OUTLINE_CLUSTER_COUNT_MIN = 1
OUTLINE_CLUSTER_COUNT_MAX = 50


def _clamp_float(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _looks_like_hex(s: str) -> bool:
    if not s:
        return False
    if len(s) not in (4, 7):
        return False
    if s[0] != "#":
        return False
    return all(c in "0123456789abcdefABCDEF" for c in s[1:])


def _coerce_axes(value, fallback: tuple[float, float, float]) -> list[float]:
    try:
        v = list(value)[:3]
    except TypeError:
        v = []
    while len(v) < 3:
        v.append(fallback[len(v)])
    out: list[float] = []
    for x in v[:3]:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _clamp_shape(shape: dict, idx: int) -> dict:
    sid = (shape.get("id") or "").strip() or f"s_{idx}"

    kind = shape.get("shape")
    if kind not in SHAPE_KINDS:
        kind = "sphere"

    material = shape.get("material")
    if material not in MATERIALS:
        material = "matte"

    motion = shape.get("motion")
    if motion not in MOTIONS:
        motion = "still"

    color = (shape.get("color") or "").strip()
    if not _looks_like_hex(color):
        color = "#111111"

    pos = _coerce_axes(shape.get("position"), (0.0, 0.0, 0.0))
    pos[0] = _clamp_float(pos[0], -2.5, 2.5)
    pos[1] = _clamp_float(pos[1], -1.6, 1.6)
    pos[2] = _clamp_float(pos[2], -1.0, 1.0)

    size = _coerce_axes(shape.get("size"), (1.0, 1.0, 1.0))
    for j in range(3):
        if size[j] <= 0:
            size[j] = 1.0
        size[j] = _clamp_float(size[j], 0.3, 4.0)

    try:
        scale = float(shape.get("scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    scale = _clamp_float(scale, 0.4, 2.4)

    out = {
        "id": sid,
        "shape": kind,
        "color": color,
        "material": material,
        "motion": motion,
        "position": pos,
        "size": size,
        "scale": scale,
    }
    name = clamp_runes(shape.get("name") or "", 40)
    if name:
        out["name"] = name
    rotation = shape.get("rotation")
    if rotation is not None:
        rot = _coerce_axes(rotation, (0.0, 0.0, 0.0))
        for j in range(3):
            rot[j] = _clamp_float(rot[j], -3.2, 3.2)
        out["rotation"] = rot
    return out


def _clamp_text(text: dict, idx: int) -> dict:
    tid = (text.get("id") or "").strip() or f"t_{idx}"
    content = clamp_runes(text.get("content") or "", TEXT_MAX_RUNES)

    font = text.get("font")
    if font not in FONTS:
        font = "sans"

    align = text.get("align")
    if align not in TEXT_ALIGNS:
        align = "center"

    material = text.get("material")
    if material not in MATERIALS:
        material = "matte"

    motion = text.get("motion")
    if motion not in MOTIONS:
        motion = "still"

    color = (text.get("color") or "").strip()
    if not _looks_like_hex(color):
        color = "#111111"

    pos = _coerce_axes(text.get("position"), (0.0, 0.0, 0.0))
    pos[0] = _clamp_float(pos[0], -2.5, 2.5)
    pos[1] = _clamp_float(pos[1], -1.6, 1.6)
    pos[2] = _clamp_float(pos[2], -1.0, 1.0)

    try:
        scale = float(text.get("scale", 1.4))
    except (TypeError, ValueError):
        scale = 1.4
    if scale <= 0:
        scale = 1.4
    scale = _clamp_float(scale, 0.8, 2.4)

    out = {
        "id": tid,
        "content": content,
        "font": font,
        "align": align,
        "color": color,
        "material": material,
        "motion": motion,
        "position": pos,
        "scale": scale,
    }
    name = clamp_runes(text.get("name") or "", 40)
    if name:
        out["name"] = name
    rotation = text.get("rotation")
    if rotation is not None:
        rot = _coerce_axes(rotation, (0.0, 0.0, 0.0))
        for j in range(3):
            rot[j] = _clamp_float(rot[j], -3.2, 3.2)
        out["rotation"] = rot
    return out


def _clamp_cluster(cluster: dict, idx: int) -> dict:
    cid = (cluster.get("id") or "").strip() or f"c{idx}"

    role = cluster.get("role")
    if role not in CLUSTER_ROLES:
        role = "background"

    kind = cluster.get("shapeKind")
    if kind not in SHAPE_KINDS:
        kind = "sphere"

    material = cluster.get("material")
    if material not in MATERIALS:
        material = "matte"

    motion = cluster.get("motion")
    if motion not in MOTIONS:
        motion = "still"

    region = cluster.get("region")
    if region not in REGIONS:
        region = "scattered"

    color = (cluster.get("colorAnchor") or "").strip()
    if not _looks_like_hex(color):
        color = "#111111"

    try:
        count = int(cluster.get("count", 1))
    except (TypeError, ValueError):
        count = 1
    count = max(OUTLINE_CLUSTER_COUNT_MIN, min(OUTLINE_CLUSTER_COUNT_MAX, count))

    raw_range = cluster.get("scaleRange") or [0.5, 1.0]
    try:
        lo = float(raw_range[0])
        hi = float(raw_range[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = 0.5, 1.0
    lo = _clamp_float(lo, 0.4, 2.4)
    hi = _clamp_float(hi, 0.4, 2.4)
    if lo > hi:
        lo, hi = hi, lo
    if role == "hero":
        # Force hero scale band high
        lo = max(lo, 1.2)
        hi = max(hi, 1.4)

    return {
        "id": cid,
        "role": role,
        "shapeKind": kind,
        "colorAnchor": color,
        "material": material,
        "motion": motion,
        "region": region,
        "count": count,
        "scaleRange": [lo, hi],
    }


def validate_outline(outline: dict) -> dict:
    """Sanitize a stage-1 outline. Hard errors raise OutlineValidationError."""
    if not isinstance(outline, dict):
        raise OutlineValidationError("outline is not an object")

    palette = outline.get("paletteId")
    if palette not in PALETTES:
        raise OutlineValidationError(f"invalid paletteId: {palette!r}")

    title = clamp_runes(outline.get("title") or "", TITLE_MAX_RUNES)
    if not title:
        raise OutlineValidationError("empty title")

    layout = outline.get("layout")
    if layout not in LAYOUTS:
        layout = "dense-constellation"

    try:
        density = int(outline.get("densityTarget", 60))
    except (TypeError, ValueError):
        density = 60
    density = max(OUTLINE_DENSITY_MIN, min(OUTLINE_DENSITY_MAX, density))

    raw_clusters = outline.get("clusters") or []
    if not isinstance(raw_clusters, list) or not raw_clusters:
        raise OutlineValidationError("outline has no clusters")
    raw_clusters = raw_clusters[:OUTLINE_CLUSTER_MAX]
    clusters = [_clamp_cluster(c if isinstance(c, dict) else {}, i) for i, c in enumerate(raw_clusters)]

    if len(clusters) < OUTLINE_CLUSTER_MIN:
        raise OutlineValidationError(f"too few clusters ({len(clusters)})")

    total = sum(c["count"] for c in clusters)
    if total < OUTLINE_DENSITY_MIN or total > OUTLINE_DENSITY_MAX:
        # rescale counts proportionally to fit density bounds
        target = max(OUTLINE_DENSITY_MIN, min(OUTLINE_DENSITY_MAX, density or total))
        scale = target / max(total, 1)
        running = 0
        for c in clusters[:-1]:
            c["count"] = max(1, int(round(c["count"] * scale)))
            running += c["count"]
        clusters[-1]["count"] = max(1, target - running)

    has_hero = any(c["role"] == "hero" for c in clusters)
    if not has_hero:
        clusters[0]["role"] = "hero"
        clusters[0]["scaleRange"] = [max(clusters[0]["scaleRange"][0], 1.3), max(clusters[0]["scaleRange"][1], 1.6)]
        clusters[0]["count"] = min(clusters[0]["count"], 2)

    out: dict = {
        "paletteId": palette,
        "title": title,
        "layout": layout,
        "densityTarget": density,
        "clusters": clusters,
    }
    background = (outline.get("background") or "").strip()
    if background and _looks_like_hex(background):
        out["background"] = background
    ai_notes = clamp_runes(outline.get("aiNotes") or "", AINOTES_MAX_RUNES)
    if ai_notes:
        out["aiNotes"] = ai_notes

    raw_texts = outline.get("texts") or []
    if isinstance(raw_texts, list) and raw_texts:
        out["texts"] = [_clamp_text(t if isinstance(t, dict) else {}, i) for i, t in enumerate(raw_texts[:TEXT_MAX_COUNT])]
    else:
        out["texts"] = []

    return out


def validate_and_clamp_scene(scene: dict) -> dict:
    """Return a sanitized scene. Raises SceneValidationError on hard failures."""
    if not isinstance(scene, dict):
        raise SceneValidationError("scene is not an object")

    out: dict = {"version": SCENE_VERSION}

    palette = scene.get("paletteId")
    if palette not in PALETTES:
        raise SceneValidationError(f"invalid paletteId: {palette!r}")
    out["paletteId"] = palette

    title = clamp_runes(scene.get("title") or "", TITLE_MAX_RUNES)
    if not title:
        raise SceneValidationError("empty title")
    out["title"] = title

    background = (scene.get("background") or "").strip()
    if background:
        out["background"] = background

    ai_notes = clamp_runes(scene.get("aiNotes") or "", AINOTES_MAX_RUNES)
    if ai_notes:
        out["aiNotes"] = ai_notes

    raw_shapes = scene.get("shapes") or []
    if not isinstance(raw_shapes, list) or not raw_shapes:
        raise SceneValidationError("scene has no shapes")
    raw_shapes = raw_shapes[:SHAPE_MAX_COUNT]
    shapes = [_clamp_shape(s if isinstance(s, dict) else {}, i) for i, s in enumerate(raw_shapes)]
    if len(shapes) < SHAPE_MIN_COUNT:
        raise SceneValidationError("too few shapes after sanitize")
    out["shapes"] = shapes

    raw_texts = scene.get("texts") or []
    if isinstance(raw_texts, list):
        raw_texts = raw_texts[:TEXT_MAX_COUNT]
        out["texts"] = [_clamp_text(t if isinstance(t, dict) else {}, i) for i, t in enumerate(raw_texts)]
    else:
        out["texts"] = []

    return out
