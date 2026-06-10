"""Cafe review endpoints.

Public (AllowAny): list + detail + tag facets of *published* reviews.
Admin (IsAdminToken): full CRUD over all reviews + image upload.

Mirrors apps.content's public-read / admin-write split and apps.habits' ViewSet
style.
"""

from __future__ import annotations

from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminToken

from .models import CafeAgentRun, CafeReview
from .serializers import (
    AdminCafeReviewListSerializer,
    CafeAgentRunSerializer,
    CafeReviewDetailSerializer,
    CafeReviewListSerializer,
    CafeReviewWriteSerializer,
)

_SORTS = {
    "recent": "-published_at",
    "rating": "-rating_overall",
    "name": "name",
}


# ── Public ───────────────────────────────────────────────────────────────---

class PublicCafeReviewListView(ListAPIView):
    """GET /api/v1/cafe/reviews  — ?q=&tag=&district=&sort=recent|rating|name"""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = CafeReviewListSerializer

    def get_queryset(self):
        qs = CafeReview.objects.filter(is_published=True)
        params = self.request.query_params

        q = (params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(excerpt__icontains=q)
                | Q(address__icontains=q)
                | Q(content_md__icontains=q)
            )

        tag = (params.get("tag") or "").strip().lower()
        if tag:
            # JSON array membership — works on both MySQL and SQLite via contains.
            qs = qs.filter(tags__contains=tag)

        district = (params.get("district") or "").strip()
        if district:
            qs = qs.filter(district__iexact=district)

        sort = _SORTS.get((params.get("sort") or "recent").strip(), "-published_at")
        return qs.order_by(sort, "-created_at")


class PublicCafeReviewDetailView(RetrieveAPIView):
    """GET /api/v1/cafe/reviews/{slug}"""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = CafeReviewDetailSerializer
    lookup_field = "slug"
    queryset = CafeReview.objects.filter(is_published=True)


class PublicCafeTagsView(APIView):
    """GET /api/v1/cafe/tags — distinct tags / amenities / districts for filter UI."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, _request):
        rows = CafeReview.objects.filter(is_published=True).values_list(
            "tags", "amenities", "district"
        )
        tags: set[str] = set()
        amenities: set[str] = set()
        districts: set[str] = set()
        for t, a, d in rows:
            tags.update(x for x in (t or []) if isinstance(x, str))
            amenities.update(x for x in (a or []) if isinstance(x, str))
            if d:
                districts.add(d)
        return Response(
            {
                "tags": sorted(tags),
                "amenities": sorted(amenities),
                "districts": sorted(districts),
            }
        )


# ── Admin ────────────────────────────────────────────────────────────────---

class AdminCafeReviewViewSet(viewsets.ModelViewSet):
    """CRUD over all reviews (incl. drafts). /api/v1/admin/cafe/reviews"""

    permission_classes = [IsAdminToken]
    queryset = CafeReview.objects.all()

    def get_serializer_class(self):
        if self.action == "list":
            return AdminCafeReviewListSerializer
        return CafeReviewWriteSerializer


class AdminCafeAgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Agent runs: observe + trigger. /api/v1/admin/cafe/agent/runs

    POST creates the run row, queues the slow work on Celery, and returns 201
    immediately — the admin UI polls GET /{id} until status leaves `started`.
    """

    permission_classes = [IsAdminToken]
    serializer_class = CafeAgentRunSerializer
    queryset = CafeAgentRun.objects.select_related("review").all()

    def create(self, request):
        from .tasks import run_cafe_agent_now

        run = CafeAgentRun.objects.create(slot="manual", status="started")
        run_cafe_agent_now.delay(run.id)
        return Response(
            CafeAgentRunSerializer(run).data, status=status.HTTP_201_CREATED
        )


class AdminCafeImageUploadView(APIView):
    """POST /api/v1/admin/cafe/uploads/image — optimize + store, return its URL."""

    permission_classes = [IsAdminToken]
    parser_classes = [MultiPartParser, FormParser]

    MAX_BYTES = 10 * 1024 * 1024  # 10 MB raw upload

    def post(self, request):
        from apps.core.uploads import DEFAULT_MAX_DIM, NotAnImage, absolute_https_url, store_image

        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": "Thiếu 'file' trong form."}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > self.MAX_BYTES:
            return Response({"error": "Ảnh quá lớn (tối đa 10 MB)."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            max_dim = int(request.data.get("max_dim") or DEFAULT_MAX_DIM)
        except (TypeError, ValueError):
            max_dim = DEFAULT_MAX_DIM
        max_dim = max(64, min(max_dim, 4096))

        try:
            meta = store_image(upload.read(), prefix="cafe", max_dim=max_dim)
        except NotAnImage:
            return Response({"error": "File không phải ảnh hợp lệ."}, status=status.HTTP_400_BAD_REQUEST)

        meta["url"] = absolute_https_url(request, meta["url"])
        return Response(meta, status=status.HTTP_201_CREATED)
