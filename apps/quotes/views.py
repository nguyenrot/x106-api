from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.accounts.auth import JWTCookieAuthentication
from apps.core.auth import ServiceTokenAuthentication, ServiceUser
from apps.core.permissions import IsAdminOrAllowedService
from apps.core.tz import local_today

from .models import Quote, QuoteAgentRun, QuoteFavorite, compute_dedup_hash
from .serializers import (
    AdminUpsertQuoteSerializer,
    AgentRunSerializer,
    QuoteSerializer,
    UpsertQuoteSerializer,
)


def _filter_public_quotes(qs, p):
    """Apply ?author=&tag=&lang=&q= filters to a curated-public queryset."""
    author = (p.get("author") or "").strip()
    if author:
        qs = qs.filter(author__icontains=author)

    tag = (p.get("tag") or "").strip().lower()
    if tag:
        qs = qs.filter(tags__contains=[tag])

    lang = (p.get("lang") or "").strip().lower()
    if lang in ("vi", "en"):
        qs = qs.filter(language=lang)

    q = (p.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(body__icontains=q) | Q(author__icontains=q) | Q(source__icontains=q))

    return qs


def _favorited_ids(user, queryset_ids) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    return set(
        QuoteFavorite.objects
        .filter(user=user, quote_id__in=list(queryset_ids))
        .values_list("quote_id", flat=True)
    )


class QuoteViewSet(viewsets.ViewSet):
    """/api/v1/quotes/ — public browse + user-authored highlights.

    GET    /quotes/featured        -> quote of the day (deterministic, VN tz)   public
    GET    /quotes/                -> list curated public quotes, filters       public
    GET    /quotes/{id}            -> quote detail                              public
    POST   /quotes/                -> user submit (defaults to private)         auth
    GET    /quotes/me/highlights   -> caller's own quotes (public+private)      auth
    PATCH  /quotes/me/highlights/{id} | DELETE                                  auth
    GET    /quotes/me/favorites    -> caller's saved favorites                  auth
    POST   /quotes/me/favorites/{quote_id} | DELETE                             auth
    """

    permission_classes = [AllowAny]
    lookup_field = "id"

    def list(self, request):
        qs = Quote.objects.filter(is_curated=True, is_public=True)
        qs = _filter_public_quotes(qs, request.query_params)
        qs = qs.order_by("-created_at")[:200]

        ids = [q.id for q in qs]
        fav_ids = _favorited_ids(request.user, ids)
        ctx = {"request": request, "favorited_ids": fav_ids}
        return Response(QuoteSerializer(qs, many=True, context=ctx).data)

    def retrieve(self, request, id=None):
        quote = Quote.objects.filter(id=id).first()
        if not quote:
            raise NotFound("Không tìm thấy quote.")

        # Visibility: curated public ALWAYS readable; private only to owner.
        if not (quote.is_curated and quote.is_public):
            if not request.user or not request.user.is_authenticated:
                raise NotFound("Không tìm thấy quote.")
            if quote.user_id != request.user.id:
                raise NotFound("Không tìm thấy quote.")

        fav_ids = _favorited_ids(request.user, [quote.id])
        ctx = {"request": request, "favorited_ids": fav_ids}
        return Response(QuoteSerializer(quote, context=ctx).data)

    def create(self, request):
        if not request.user or not request.user.is_authenticated:
            raise PermissionDenied("Đăng nhập để submit quote.")
        serializer = UpsertQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        quote = Quote.objects.create(
            user=request.user,
            body=v["body"],
            author=v.get("author") or "",
            source=v.get("source") or "",
            tags=v.get("tags") or [],
            language=v.get("language") or "vi",
            is_public=bool(v.get("is_public")),
            is_curated=False,
            is_featured=False,
        )
        return Response(
            QuoteSerializer(quote, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"], url_path="featured", permission_classes=[AllowAny])
    def featured(self, request):
        """Quote of the day — deterministic pick over featured curated quotes.

        Falls back to most-recent curated quote if no featured ones exist."""

        pool_qs = Quote.objects.filter(is_curated=True, is_public=True, is_featured=True)
        pool_ids = list(pool_qs.values_list("id", flat=True).order_by("id"))

        if not pool_ids:
            pool_qs = Quote.objects.filter(is_curated=True, is_public=True)
            pool_ids = list(pool_qs.values_list("id", flat=True).order_by("id"))

        if not pool_ids:
            return Response(None)

        today = local_today().isoformat()
        digest = hashlib.sha256(today.encode("utf-8")).hexdigest()
        idx = int(digest, 16) % len(pool_ids)
        chosen_id = pool_ids[idx]
        quote = Quote.objects.get(id=chosen_id)

        fav_ids = _favorited_ids(request.user, [quote.id])
        ctx = {"request": request, "favorited_ids": fav_ids}
        return Response(QuoteSerializer(quote, context=ctx).data)

    @action(detail=False, methods=["get"], url_path="me/highlights", permission_classes=[IsAuthenticated])
    def my_highlights(self, request):
        qs = Quote.objects.filter(user=request.user).order_by("-created_at")
        return Response(QuoteSerializer(qs, many=True, context={"request": request}).data)

    @action(
        detail=False,
        methods=["patch", "delete"],
        url_path=r"me/highlights/(?P<quote_id>[^/.]+)",
        permission_classes=[IsAuthenticated],
    )
    def my_highlight_item(self, request, quote_id: str):
        quote = Quote.objects.filter(id=quote_id, user=request.user).first()
        if not quote:
            raise NotFound("Không tìm thấy highlight.")

        if request.method.upper() == "DELETE":
            quote.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpsertQuoteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for k, v in serializer.validated_data.items():
            setattr(quote, k, v)
        quote.save()
        return Response(QuoteSerializer(quote, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="me/favorites", permission_classes=[IsAuthenticated])
    def my_favorites(self, request):
        favs = (
            QuoteFavorite.objects
            .filter(user=request.user)
            .select_related("quote")
            .order_by("-created_at")
        )
        quotes = [f.quote for f in favs]
        ctx = {"request": request, "favorited_ids": {q.id for q in quotes}}
        return Response(QuoteSerializer(quotes, many=True, context=ctx).data)

    @action(
        detail=False,
        methods=["post", "delete"],
        url_path=r"me/favorites/(?P<quote_id>[^/.]+)",
        permission_classes=[IsAuthenticated],
    )
    def toggle_favorite(self, request, quote_id: str):
        quote = Quote.objects.filter(id=quote_id).first()
        if not quote:
            raise NotFound("Không tìm thấy quote.")

        if request.method.upper() == "DELETE":
            QuoteFavorite.objects.filter(user=request.user, quote=quote).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        QuoteFavorite.objects.get_or_create(user=request.user, quote=quote)
        return Response({"favorited": True}, status=status.HTTP_201_CREATED)


# ─── Admin endpoints ────────────────────────────────────────────────────────

class AdminQuoteViewSet(viewsets.ViewSet):
    """/api/v1/admin/quotes/ — curation surface for admin.kynguyen.cc.

    Accepts EITHER a human-admin JWT (x106_admin cookie / Bearer with
    `role:admin`) OR a service token in the `X-Service-Token` header whose
    name is in `allowed_services`. The daily quotes agent uses the service
    token path.
    """

    authentication_classes = [JWTCookieAuthentication, ServiceTokenAuthentication]
    permission_classes = [IsAdminOrAllowedService]
    allowed_services = ["quotes-agent"]
    lookup_field = "id"

    def list(self, request):
        p = request.query_params
        scope = (p.get("scope") or "all").strip()  # all | pending | curated | featured
        qs = Quote.objects.all()
        if scope == "pending":
            qs = qs.filter(is_curated=False)
        elif scope == "curated":
            qs = qs.filter(is_curated=True)
        elif scope == "featured":
            qs = qs.filter(is_featured=True)
        qs = _filter_public_quotes(qs, p).order_by("-created_at")[:500]
        return Response(QuoteSerializer(qs, many=True).data)

    def retrieve(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        return Response(QuoteSerializer(q).data)

    def create(self, request):
        """Admin / service token creates a curated quote directly.

        Accepts is_curated / is_public / is_featured in the payload (defaults
        to a fully-published quote). Duplicate dedup_hash → 409 + existing id.
        """
        serializer = AdminUpsertQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data

        body = v["body"]
        author = v.get("author") or ""
        dedup = compute_dedup_hash(body, author)
        if dedup:
            existing = Quote.objects.filter(dedup_hash=dedup).first()
            if existing:
                return Response(
                    {
                        "detail": "duplicate",
                        "existing_id": existing.id,
                        "quote": QuoteSerializer(existing).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        try:
            q = Quote.objects.create(
                user=None,
                body=body,
                author=author,
                source=v.get("source") or "",
                tags=v.get("tags") or [],
                language=v.get("language") or "en",
                is_public=v.get("is_public", True),
                is_curated=v.get("is_curated", True),
                is_featured=v.get("is_featured", False),
            )
        except IntegrityError:
            # Race: another writer inserted the same hash between our SELECT
            # and INSERT. Refetch and return the winner.
            existing = Quote.objects.filter(dedup_hash=dedup).first() if dedup else None
            if existing:
                return Response(
                    {
                        "detail": "duplicate",
                        "existing_id": existing.id,
                        "quote": QuoteSerializer(existing).data,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            raise
        return Response(QuoteSerializer(q).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        serializer = AdminUpsertQuoteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for k, v in serializer.validated_data.items():
            setattr(q, k, v)
        q.save()
        return Response(QuoteSerializer(q).data)

    def destroy(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        q.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="curate")
    def curate(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        q.is_curated = True
        q.is_public = True
        q.save(update_fields=["is_curated", "is_public", "updated_at"])
        return Response(QuoteSerializer(q).data)

    @action(detail=True, methods=["post"], url_path="uncurate")
    def uncurate(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        q.is_curated = False
        q.is_featured = False
        q.save(update_fields=["is_curated", "is_featured", "updated_at"])
        return Response(QuoteSerializer(q).data)

    @action(detail=True, methods=["post"], url_path="feature")
    def feature(self, request, id=None):
        q = Quote.objects.filter(id=id).first()
        if not q:
            raise NotFound()
        q.is_featured = not q.is_featured
        # Featuring forces curation so the quote-of-the-day pool is consistent.
        if q.is_featured:
            q.is_curated = True
            q.is_public = True
        q.save(update_fields=["is_featured", "is_curated", "is_public", "updated_at"])
        return Response(QuoteSerializer(q).data)

    # ─── Agent observability ─────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="agent-status")
    def agent_status(self, request):
        """Healthcheck for the daily quotes agent. Returns last run + 7d stats."""
        service = (request.query_params.get("service") or "quotes-agent").strip()
        last = (
            QuoteAgentRun.objects
            .filter(service_name=service)
            .order_by("-started_at")
            .first()
        )
        last_ok = (
            QuoteAgentRun.objects
            .filter(service_name=service, status__in=["succeeded", "duplicate"])
            .order_by("-started_at")
            .first()
        )
        cutoff = timezone.now() - timedelta(days=7)
        failures_7d = QuoteAgentRun.objects.filter(
            service_name=service, status="failed", started_at__gte=cutoff
        ).count()
        runs_7d = QuoteAgentRun.objects.filter(
            service_name=service, started_at__gte=cutoff
        ).count()
        return Response(
            {
                "service": service,
                "last_run": AgentRunSerializer(last).data if last else None,
                "last_success_at": last_ok.started_at if last_ok else None,
                "last_success_quote_id": last_ok.quote_id if last_ok else None,
                "runs_last_7d": runs_7d,
                "failures_last_7d": failures_7d,
                "healthy": bool(
                    last_ok
                    and last_ok.started_at >= timezone.now() - timedelta(hours=36)
                ),
            }
        )

    @action(detail=False, methods=["post"], url_path="agent-runs")
    def create_agent_run(self, request):
        """Agent records the outcome of each invocation here."""
        # If service-token caller, default service_name from token name.
        default_service = (
            request.user.name if isinstance(request.user, ServiceUser) else "quotes-agent"
        )
        serializer = AgentRunSerializer(
            data=request.data, context={"default_service": default_service}
        )
        serializer.is_valid(raise_exception=True)
        run = serializer.save()
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
