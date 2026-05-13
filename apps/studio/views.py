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
    QuotaExceeded,
)
from .models import Artwork, LLMConversation, LLMConversationMessage, LLMJob, LLMJobStatus, LLMMessageRole
from .serializers import (
    ArtworkSerializer,
    LLMQuotaSerializer,
    LLMSubmitSerializer,
    PublicArtworkSerializer,
)
from .services.model_catalog import all_models
from .settings_keys import (
    ModelDeprecated,
    ModelDisabled,
    ModelNotAllowed,
    RouterModelNotDrawable,
    allowed_flash_models,
    allowed_pro_models,
    effective_daily_limit,
    effective_flash_model,
    effective_pro_model,
    llm_enabled,
    resolve_pro_model,
)
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
        if not (settings.DEEPSEEK_API_KEY or settings.OPENCODE_API_KEY):
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

        # Validate pro_model override up front — before reserving quota — so a
        # bad model selection can't waste a daily request slot.
        try:
            resolved_pro = resolve_pro_model(body.get("proModel"))
        except RouterModelNotDrawable as exc:
            return Response(
                {
                    "error": "router_model_not_drawable",
                    "message": (
                        f'Model "{exc.label}" is a router (intent classifier), '
                        "not a drawer. Pick a different model to render 3D."
                    ),
                    "model": exc.model_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ModelDisabled as exc:
            return Response(
                {
                    "error": "model_disabled",
                    "message": f'Model "{exc.label}" is disabled. Pick a different model.',
                    "model": exc.model_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ModelDeprecated as exc:
            return Response(
                {
                    "error": "model_deprecated",
                    "message": (
                        f'Model "{exc.label}" is deprecated. Pick a current model.'
                    ),
                    "model": exc.model_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ModelNotAllowed as exc:
            return Response(
                {
                    "error": "model_not_allowed",
                    "message": "Model is not enabled for user override.",
                    "model": exc.model_id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            pro_model=resolved_pro,
        )
        async_result = run_llm_job.delay(job.id)
        # Capture the Celery task id so cancel_job can revoke(terminate=True).
        # AsyncResult.id is only populated post-enqueue, so this is a follow-up
        # UPDATE — concurrent with the worker pick-up, but that's fine: the
        # worker reads the row again before working.
        LLMJob.objects.filter(id=job.id).update(celery_task_id=async_result.id)
        return Response(
            {"jobId": job.id, "used": used, "remaining": remaining, "limit": limit},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path="models")
    def models(self, _request):
        """Phase 4.3 v2: chat picker shows only drawable models (pro/both),
        each with badges + description. Flash-only models are hidden by
        design — defense-in-depth on top of resolve_pro_model's RouterModel
        guard. Default model is flagged via `isDefault=true`.

        Legacy fields (`catalog`, `allowedFlash`, `allowedPro`, `defaultFlash`,
        `defaultPro`) are kept for one deploy cycle so older clients keep
        working while the art frontend rolls forward.
        """
        from .services.model_catalog import (
            user_picker_models,  # local import — avoid top-level cycle in case of refactor
        )
        default_pro = effective_pro_model()
        v2_models = [
            {
                "id": row["slug"],
                "name": row["display_name"],
                "description": row["description"] or "",
                "badges": {
                    "speed": row["speed_badge"] or "",
                    "quality": row["quality_badge"] or "",
                    "cost": row["cost_badge"] or "",
                },
                "beta": bool(row["beta"]),
                "isDefault": row["slug"] == default_pro,
            }
            for row in user_picker_models()
        ]
        # Legacy shape — older clients still consume these.
        catalog = [
            {"id": m.id, "label": m.label, "role": m.role, "provider": m.provider}
            for m in all_models()
        ]
        return Response(
            {
                "version": 2,
                "models": v2_models,
                "auto": {"id": None, "label": "Auto"},
                # Legacy fields.
                "catalog": catalog,
                "allowedFlash": allowed_flash_models(),
                "allowedPro": allowed_pro_models(),
                "defaultFlash": effective_flash_model(),
                "defaultPro": default_pro,
            }
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
            updated = LLMJob.objects.filter(
                id=job_id, status=LLMJobStatus.PROCESSING
            ).update(
                status=LLMJobStatus.CANCELED,
                finished_at=timezone.now(),
            )
            refunded = False
            if updated and job.celery_task_id:
                # SIGTERM the running worker so we stop wasting upstream tokens.
                # If revoke fails (broker hiccup / worker already exited), the
                # cancel still stands — the conditional update in tasks.py
                # prevents the worker from overwriting CANCELED back to DONE.
                try:
                    from celery import current_app
                    current_app.control.revoke(
                        job.celery_task_id, terminate=True, signal="SIGTERM"
                    )
                except Exception as exc:  # noqa: BLE001 — broker outage etc.
                    log.warning(
                        "cancel_job: revoke(%s) failed: %s — status still CANCELED",
                        job.celery_task_id, exc,
                    )
                # Refund quota when we successfully canceled a running job —
                # the worker won't reach its own refund path now.
                quota.refund(job.user_id)
                refunded = True
            return Response(
                {"jobId": job.id, "status": LLMJobStatus.CANCELED, "refunded": refunded}
            )

        return Response(
            {"jobId": job.id, "status": job.status, "refunded": False, "noop": True}
        )


# ─── Phase 2.1 conversation persistence ─────────────────────────────────────

CONVERSATION_CAP_PER_USER = 20  # auto-prune oldest non-pinned beyond this
MESSAGE_LIMIT_PER_CONV = 200    # per-fetch page size


class ConversationViewSet(viewsets.ViewSet):
    """/api/v1/studio/conversations — per-user chat history.

    Auto-prune at 20 non-pinned per user keeps the table tidy without forcing
    the user to manually delete old threads. Messages stay until conversation
    delete (CASCADE) so audit trail survives orphaned UI references."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        rows = (
            LLMConversation.objects
            .filter(user_id=request.user.id)
            .order_by("-pinned", "-updated_at")[:50]
        )
        items = []
        for r in rows:
            # Compute messageCount in one query per row — table sizes are small.
            count = LLMConversationMessage.objects.filter(conversation_id=r.id).count()
            items.append({
                "id": r.id,
                "title": r.title or "New chat",
                "pinned": r.pinned,
                "messageCount": count,
                "updatedAt": r.updated_at.isoformat(),
                "createdAt": r.created_at.isoformat(),
            })
        return Response({"items": items})

    def retrieve(self, request, pk: str | None = None):
        conv = LLMConversation.objects.filter(id=pk, user_id=request.user.id).first()
        if conv is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        messages = list(
            LLMConversationMessage.objects
            .filter(conversation_id=conv.id)
            .order_by("created_at")[:MESSAGE_LIMIT_PER_CONV]
        )
        return Response({
            "id": conv.id,
            "title": conv.title or "New chat",
            "pinned": conv.pinned,
            "createdAt": conv.created_at.isoformat(),
            "updatedAt": conv.updated_at.isoformat(),
            "messages": [_serialize_message(m) for m in messages],
        })

    def create(self, request):
        title = (request.data.get("title") or "")[:120]
        conv = LLMConversation.objects.create(
            user_id=request.user.id,
            title=title,
        )
        # Auto-prune: keep only 20 non-pinned per user (oldest go first).
        _prune_conversations(request.user.id)
        return Response({
            "id": conv.id,
            "title": conv.title or "New chat",
            "pinned": conv.pinned,
            "createdAt": conv.created_at.isoformat(),
            "updatedAt": conv.updated_at.isoformat(),
        }, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk: str | None = None):
        conv = LLMConversation.objects.filter(id=pk, user_id=request.user.id).first()
        if conv is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if "title" in request.data:
            conv.title = (request.data.get("title") or "")[:120]
        if "pinned" in request.data:
            conv.pinned = bool(request.data["pinned"])
        conv.save(update_fields=["title", "pinned", "updated_at"])
        return Response({
            "id": conv.id,
            "title": conv.title or "New chat",
            "pinned": conv.pinned,
            "updatedAt": conv.updated_at.isoformat(),
        })

    def destroy(self, request, pk: str | None = None):
        deleted, _ = LLMConversation.objects.filter(id=pk, user_id=request.user.id).delete()
        if deleted == 0:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"id": pk, "deleted": True})

    @action(detail=True, methods=["post"], url_path="messages")
    def append_message(self, request, pk: str | None = None):
        conv = LLMConversation.objects.filter(id=pk, user_id=request.user.id).first()
        if conv is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        role = (request.data.get("role") or "").strip()
        if role not in (LLMMessageRole.USER, LLMMessageRole.ASSISTANT, LLMMessageRole.SYSTEM):
            return Response({"error": "invalid role"}, status=status.HTTP_400_BAD_REQUEST)
        content = (request.data.get("content") or "")[:8000]
        if not content.strip() and role != LLMMessageRole.ASSISTANT:
            # Allow empty assistant content (could be a failed-turn placeholder).
            return Response({"error": "content required"}, status=status.HTTP_400_BAD_REQUEST)
        msg = LLMConversationMessage.objects.create(
            conversation_id=conv.id,
            role=role,
            content=content,
            scene_snapshot=request.data.get("sceneSnapshot"),
            applied_scene=bool(request.data.get("appliedScene", False)),
            job_id=(request.data.get("jobId") or None),
            error_kind=(request.data.get("errorKind") or None),
        )
        # Bump conversation updated_at so list endpoint sorts by recency.
        LLMConversation.objects.filter(id=conv.id).update(updated_at=timezone.now())
        # Auto-fill title from first user message if conv title is empty.
        if not conv.title and role == LLMMessageRole.USER and content.strip():
            LLMConversation.objects.filter(id=conv.id, title="").update(title=content[:60])
        return Response(_serialize_message(msg), status=status.HTTP_201_CREATED)


def _serialize_message(m: LLMConversationMessage) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "sceneSnapshot": m.scene_snapshot,
        "appliedScene": m.applied_scene,
        "jobId": m.job_id or None,
        "errorKind": m.error_kind or None,
        "createdAt": m.created_at.isoformat(),
    }


def _prune_conversations(user_id: str) -> None:
    """Delete non-pinned conversations beyond the cap, oldest-first. Cheap —
    runs after each create. CASCADE handles the messages."""
    keep_ids = list(
        LLMConversation.objects
        .filter(user_id=user_id, pinned=False)
        .order_by("-updated_at")
        .values_list("id", flat=True)[:CONVERSATION_CAP_PER_USER]
    )
    LLMConversation.objects.filter(
        user_id=user_id, pinned=False,
    ).exclude(id__in=keep_ids).delete()
