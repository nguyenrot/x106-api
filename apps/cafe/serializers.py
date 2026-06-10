from __future__ import annotations

import markdown as md
import nh3
from django.utils.timezone import now
from rest_framework import serializers

from apps.core.text import slugify_vi

from .models import CafeReview


def _normalize_slugs(values: list, *, max_items: int = 24) -> list[str]:
    """Lower-cased, de-duped, slugified short tokens (tags/amenities)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        if not isinstance(raw, str):
            continue
        t = slugify_vi(raw, max_chars=40)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_items:
            break
    return out


def _normalize_urls(values: list, *, max_items: int = 30) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        if isinstance(raw, str) and raw.strip().startswith(("http://", "https://")):
            out.append(raw.strip())
        if len(out) >= max_items:
            break
    return out


# The "extra" extension passes raw inline HTML straight through, and the
# rendered result is v-html'd on the public site — sanitize server-side with
# an allowlist of standard markdown output tags.
_ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}


def _render_md(text: str) -> str:
    if not text:
        return ""
    html = md.markdown(text, extensions=["extra", "sane_lists", "nl2br"])
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


# ── Public read serializers ─────────────────────────────────────────────────

class CafeReviewListSerializer(serializers.ModelSerializer):
    """Compact shape for the public feed/grid."""

    class Meta:
        model = CafeReview
        fields = [
            "id", "slug", "name", "excerpt", "district",
            "price_level", "price_note", "tags",
            "rating_overall", "cover_image_url",
            "lat", "lng",
            "visited_at", "published_at",
        ]


class CafeReviewDetailSerializer(serializers.ModelSerializer):
    """Full shape for a single review page (serves rendered HTML)."""

    class Meta:
        model = CafeReview
        fields = [
            "id", "slug", "name", "excerpt",
            "address", "district", "lat", "lng",
            "price_level", "price_note", "opening_hours", "amenities", "tags",
            "rating_overall", "rating_drink", "rating_space", "rating_price", "rating_service",
            "cover_image_url", "gallery",
            "content_html",
            "is_published", "visited_at", "published_at", "created_at", "updated_at",
        ]


class AdminCafeReviewListSerializer(serializers.ModelSerializer):
    """Compact admin list — includes drafts + status, no heavy content."""

    class Meta:
        model = CafeReview
        fields = [
            "id", "slug", "name", "district", "is_published",
            "rating_overall", "cover_image_url",
            "visited_at", "published_at", "updated_at",
        ]


# ── Admin write serializer ──────────────────────────────────────────────────

class CafeReviewWriteSerializer(serializers.ModelSerializer):
    """Create/update from the admin editor. Returns the full detail shape +
    `content_md` so the editor can re-hydrate."""

    class Meta:
        model = CafeReview
        fields = [
            "id", "slug", "name", "excerpt",
            "address", "district", "lat", "lng",
            "price_level", "price_note", "opening_hours", "amenities", "tags",
            "rating_overall", "rating_drink", "rating_space", "rating_price", "rating_service",
            "cover_image_url", "gallery",
            "content_md", "content_html",
            "is_published", "visited_at", "published_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "content_html", "published_at", "created_at", "updated_at"]
        extra_kwargs = {"slug": {"required": False}}

    def validate_amenities(self, value):
        return _normalize_slugs(value)

    def validate_tags(self, value):
        return _normalize_slugs(value)

    def validate_gallery(self, value):
        return _normalize_urls(value)

    def validate_price_level(self, value):
        if value is not None and not 1 <= value <= 4:
            raise serializers.ValidationError("price_level phải từ 1 đến 4.")
        return value

    def _validate_rating(self, value, field):
        if value is None:
            return value
        if not 0 <= value <= 5:
            raise serializers.ValidationError({field: "Điểm phải từ 0 đến 5."})
        return value

    def validate(self, attrs):
        for f in ("rating_overall", "rating_drink", "rating_space", "rating_price", "rating_service"):
            if f in attrs:
                self._validate_rating(attrs[f], f)
        return attrs

    def _unique_slug(self, base: str, *, exclude_pk: str | None = None) -> str:
        base = base or "quan"
        slug = base
        i = 2
        qs = CafeReview.objects.all()
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        while qs.filter(slug=slug).exists():
            slug = f"{base}-{i}"
            i += 1
        return slug

    def _apply_derived(self, validated, instance=None):
        # Slug: honor an explicit slug, else derive from name. Keep stable on edit
        # unless name changed and slug was auto-derived.
        explicit = validated.get("slug")
        name = validated.get("name") or (instance.name if instance else "")
        if explicit:
            base = slugify_vi(explicit) or slugify_vi(name)
            validated["slug"] = self._unique_slug(base, exclude_pk=instance.pk if instance else None)
        elif instance is None:
            validated["slug"] = self._unique_slug(slugify_vi(name))
        else:
            validated.pop("slug", None)  # don't churn an existing slug on plain edits

        # Render markdown → html whenever content_md is part of the write.
        if "content_md" in validated:
            validated["content_html"] = _render_md(validated["content_md"])

        # Stamp published_at on the publish transition.
        will_publish = validated.get(
            "is_published", instance.is_published if instance else False
        )
        already = instance.published_at if instance else None
        if will_publish and already is None:
            validated["published_at"] = now()
        if not will_publish:
            validated["published_at"] = None
        return validated

    def create(self, validated_data):
        validated_data = self._apply_derived(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data = self._apply_derived(validated_data, instance=instance)
        return super().update(instance, validated_data)
