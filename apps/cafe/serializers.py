from __future__ import annotations

import markdown as md
import nh3
from django.utils.timezone import now
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.text import slugify_vi

from .imaging import riso_map
from .models import CafeAgentRun, CafeReview


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

class RisoVariantSerializer(serializers.Serializer):
    """Read-only shape of one stored duotone pair (see `apps.cafe.imaging`).

    Declared so the OpenAPI schema describes `cover_riso` / `gallery_riso`
    properly instead of falling back to a bare object. Never parses input —
    variants are derived at upload time, never posted.
    """

    src = serializers.URLField(help_text="1x duotone WebP")
    src2x = serializers.URLField(help_text="Retina duotone WebP")
    w = serializers.IntegerField(allow_null=True)
    h = serializers.IntegerField(allow_null=True)


class _RisoCoverListSerializer(serializers.ListSerializer):
    """Fills every row's `cover_riso` from a single query.

    Resolving the variant inside the child serializer would issue one lookup per
    review, and the public feed serializes the whole published list in one
    response — a textbook N+1 against a table that is only ever read by URL.
    """

    def to_representation(self, data):
        rows = super().to_representation(data)
        variants = riso_map(row.get("cover_image_url") for row in rows)
        for row in rows:
            row["cover_riso"] = variants.get(row.get("cover_image_url") or "")
        return rows


class CafeReviewListSerializer(serializers.ModelSerializer):
    """Compact shape for the public feed/grid."""

    cover_riso = serializers.SerializerMethodField()

    class Meta:
        model = CafeReview
        list_serializer_class = _RisoCoverListSerializer
        fields = [
            "id", "slug", "name", "excerpt", "district",
            "price_level", "price_note", "tags",
            # `amenities` rides along so the index can filter on it in the
            # browser. Django's public list endpoint has no amenity query param
            # and the whole published feed is under a hundred rows, so adding a
            # JSON-membership filter server-side would cost a round-trip per
            # checkbox to save nothing.
            "amenities",
            "rating_overall", "cover_image_url", "cover_riso",
            "lat", "lng",
            "visited_at", "published_at",
        ]

    @extend_schema_field(RisoVariantSerializer(allow_null=True))
    def get_cover_riso(self, obj):
        """Bulk-resolved by the parent when serializing a list; a standalone
        instance (rare — the detail view has its own serializer) pays one query."""
        if isinstance(self.parent, _RisoCoverListSerializer):
            return None  # the parent overwrites this once the page is built
        return riso_map([obj.cover_image_url]).get(obj.cover_image_url or "")


class CafeReviewDetailSerializer(serializers.ModelSerializer):
    """Full shape for a single review page (serves rendered HTML)."""

    cover_riso = serializers.SerializerMethodField()
    gallery_riso = serializers.SerializerMethodField()

    class Meta:
        model = CafeReview
        fields = [
            "id", "slug", "name", "excerpt",
            "address", "district", "lat", "lng",
            "price_level", "price_note", "opening_hours", "amenities", "tags",
            "rating_overall", "rating_drink", "rating_space", "rating_price", "rating_service",
            "cover_image_url", "cover_riso",
            "gallery", "gallery_riso",
            "content_html",
            "is_published", "visited_at", "published_at", "created_at", "updated_at",
        ]

    def _variants(self, obj) -> dict:
        """Duotone variants for the cover *and* every gallery shot, in one query.

        Memoized on the serializer so the two field callbacks below share a
        single lookup instead of hitting the table twice per review.
        """
        cached = getattr(self, "_variant_cache", None)
        if cached is None or cached[0] is not obj:
            cached = (obj, riso_map([obj.cover_image_url, *(obj.gallery or [])]))
            self._variant_cache = cached
        return cached[1]

    @extend_schema_field(RisoVariantSerializer(allow_null=True))
    def get_cover_riso(self, obj):
        return self._variants(obj).get(obj.cover_image_url or "")

    @extend_schema_field(RisoVariantSerializer(many=True))
    def get_gallery_riso(self, obj):
        """Index-aligned with `gallery`, null wherever a photo predates the
        pipeline — the frontend falls back to the colour master at that index."""
        variants = self._variants(obj)
        return [variants.get(url) for url in (obj.gallery or [])]


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


# ── Agent runs (admin observability + manual trigger) ───────────────────────

class CafeAgentRunSerializer(serializers.ModelSerializer):
    """Status shape the admin UI polls. Raw agy transcripts stay out of the
    API payload (they can reach 200 KB) — Django admin has them if needed."""

    review_slug = serializers.SlugField(source="review.slug", read_only=True, allow_null=True)

    class Meta:
        model = CafeAgentRun
        fields = [
            "id", "slot", "status", "cafe_name",
            "review_id", "review_slug",
            "error_message", "validation_error",
            "duration_ms", "agy_duration_ms",
            "started_at", "ended_at",
        ]
        read_only_fields = fields
