"""LLMScene v3 validator/clamp.

The renderer (frontend) only handles the enums in these whitelists. Anything
out-of-vocabulary gets rewritten to a safe default; out-of-bbox positions are
clamped; arrays beyond the cap are truncated. Hard errors (bad palette, no
shapes) raise SceneValidationError so the worker can retry."""

from __future__ import annotations

from apps.core.text import clamp_runes

from ..errors import SceneValidationError

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

SCENE_VERSION = 3
TITLE_MAX_RUNES = 40
AINOTES_MAX_RUNES = 200
TEXT_MAX_RUNES = 120
SHAPE_MAX_COUNT = 100
SHAPE_MIN_COUNT = 1
TEXT_MAX_COUNT = 4


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


CHAT_MESSAGE_MAX_RUNES = 200


def validate_chat_response(parsed: dict) -> tuple[dict | None, str]:
    """Validate a chat-mode response: { scene: LLMScene | null, message: str }.

    Raises SceneValidationError on hard failures (missing message, scene
    present-but-malformed). Null scene is fine — chat may reply without
    changing the canvas."""
    if not isinstance(parsed, dict):
        raise SceneValidationError("chat response is not an object")

    raw_message = parsed.get("message")
    if not isinstance(raw_message, str) or not raw_message.strip():
        raise SceneValidationError("chat response missing 'message'")
    message = clamp_runes(raw_message.strip(), CHAT_MESSAGE_MAX_RUNES)

    raw_scene = parsed.get("scene")
    if raw_scene is None:
        return (None, message)
    if not isinstance(raw_scene, dict):
        raise SceneValidationError("chat 'scene' is not an object or null")
    scene = validate_and_clamp_scene(raw_scene)
    return (scene, message)
