"""Studio HTTP views — artworks (CRUD) + LLM (quota, job submit/get/cancel).

Greenfield: the synchronous /random, /polish, /remix endpoints from the Go
service are gone. The frontend submits a job and polls — there is no path that
holds an HTTP request open longer than ~1s.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.ids import new_id

from . import quota
from .errors import (
    LLMDisabledError,
    LLMOffError,
    QuotaExceeded,
)
from .models import Artwork, LLMJob, LLMJobStatus
from .serializers import (
    ArtworkSerializer,
    LLMQuotaSerializer,
    LLMSubmitSerializer,
    PublicArtworkSerializer,
)
from .settings_keys import effective_daily_limit, llm_enabled
from .tasks import run_llm_job

log = logging.getLogger("x106.studio.views")


class ArtworkViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ArtworkSerializer
    http_method_names = ["get", "post", "delete"]
    pagination_class = None

    def get_queryset(self):
        # Slice only on list — retrieve/destroy need an unsliced queryset for .get(pk=...)
        qs = Artwork.objects.filter(user=self.request.user).order_by("-created_at")
        if self.action == "list":
            return qs[:60]
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, id=new_id())

    @action(detail=True, methods=["post", "delete"], url_path="share")
    def share(self, request, pk: str | None = None):
        """POST → ensure a share token exists (idempotent); DELETE → revoke.

        Token is `secrets.token_urlsafe(16)` (≈22 url-safe chars, 128 bits of
        entropy). Returns `{shareToken, shareUrl}` on POST, `{shareToken: null}`
        on DELETE."""
        artwork = self.get_queryset().filter(pk=pk).first()
        if artwork is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            if artwork.share_token:
                artwork.share_token = None
                artwork.save(update_fields=["share_token", "updated_at"])
            return Response({"shareToken": None})

        if not artwork.share_token:
            # Retry on the (vanishingly rare) UNIQUE collision instead of bubbling up a 500.
            for _ in range(5):
                token = secrets.token_urlsafe(16)
                artwork.share_token = token
                try:
                    artwork.save(update_fields=["share_token", "updated_at"])
                    break
                except Exception:  # IntegrityError + driver-specific subclasses
                    artwork.share_token = None
            else:
                return Response(
                    {"error": "could not allocate share token"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
        return Response({"shareToken": artwork.share_token})


class PublicArtworkView(APIView):
    """GET /api/v1/public/artworks/<token> — anonymous, returns scene + minimal metadata."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, _request, token: str):
        # No select_related("user"): the legacy `users.id` is utf8mb4_0900_ai_ci
        # while `artworks.user_id` is utf8mb4_unicode_ci, so any JOIN on those
        # raises 1267. The serializer's `source="user.username"` triggers a lazy
        # single-table lookup, which is fine.
        artwork = Artwork.objects.filter(share_token=token).first()
        if artwork is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(PublicArtworkSerializer(artwork).data)


def _quota_payload(user_id: str) -> dict:
    limit = effective_daily_limit()
    used, remaining = quota.get_quota(user_id, limit)
    return {"used": used, "remaining": remaining, "limit": limit}


class LLMViewSet(viewsets.ViewSet):
    """`/api/v1/studio/llm/...` — quota + async job pipeline."""

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="quota")
    def quota(self, request):
        return Response(LLMQuotaSerializer(_quota_payload(request.user.id)).data)

    @action(detail=False, methods=["post"], url_path="job")
    def submit_job(self, request):
        if not settings.DEEPSEEK_API_KEY:
            return Response(
                {"error": "AI mode not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not llm_enabled():
            return Response(
                {"error": "AI mode disabled by admin"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = LLMSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_data

        limit = effective_daily_limit()
        try:
            used, remaining = quota.reserve(request.user.id, limit)
        except QuotaExceeded:
            return Response(
                {"error": "quota exceeded", "limit": limit, "remaining": 0},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        request_body_payload: dict = {
            "currentScene": body.get("currentScene"),
            "userMessage": body["userMessage"],
        }
        if body.get("history"):
            request_body_payload["history"] = body["history"]

        job = LLMJob.objects.create(
            id=new_id(),
            user_id=request.user.id,
            username=request.user.username,
            mode=body["mode"],
            request_body=request_body_payload,
        )
        run_llm_job.delay(job.id)
        return Response(
            {"jobId": job.id, "used": used, "remaining": remaining, "limit": limit},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path=r"job/(?P<job_id>[^/]+)")
    def get_job(self, request, job_id: str | None = None):
        job = LLMJob.objects.filter(id=job_id, user_id=request.user.id).first()
        if job is None:
            return Response({"error": "job not found"}, status=status.HTTP_404_NOT_FOUND)

        elapsed_ms = 0
        if job.started_at:
            end = job.finished_at or timezone.now()
            elapsed_ms = max(int((end - job.started_at).total_seconds() * 1000), 0)

        payload = {
            "jobId": job.id,
            "status": job.status,
            "mode": job.mode,
            "elapsedMs": elapsed_ms,
            **_quota_payload(request.user.id),
        }
        if job.status == LLMJobStatus.DONE:
            if job.result_scene:
                payload["scene"] = job.result_scene
            if job.result_message:
                payload["assistantMessage"] = job.result_message
        if job.status in {LLMJobStatus.FAILED, LLMJobStatus.CANCELED} and job.error_message:
            payload["errorMessage"] = job.error_message
        return Response(payload)

    @action(detail=False, methods=["post"], url_path=r"job/(?P<job_id>[^/]+)/cancel")
    def cancel_job(self, request, job_id: str | None = None):
        job = LLMJob.objects.filter(id=job_id, user_id=request.user.id).first()
        if job is None:
            return Response({"error": "job not found"}, status=status.HTTP_404_NOT_FOUND)

        if job.status == LLMJobStatus.PENDING:
            updated = LLMJob.objects.filter(id=job_id, status=LLMJobStatus.PENDING).update(
                status=LLMJobStatus.CANCELED,
                finished_at=timezone.now(),
            )
            if updated:
                quota.refund(job.user_id)
            return Response({"jobId": job.id, "status": LLMJobStatus.CANCELED, "refunded": bool(updated)})

        if job.status == LLMJobStatus.PROCESSING:
            LLMJob.objects.filter(id=job_id, status=LLMJobStatus.PROCESSING).update(
                status=LLMJobStatus.CANCELED,
                finished_at=timezone.now(),
            )
            return Response({"jobId": job.id, "status": LLMJobStatus.CANCELED, "refunded": False})

        return Response(
            {"jobId": job.id, "status": job.status, "refunded": False, "noop": True}
        )
