"""Admin endpoints for the AI art studio.

Mirrors internal/handler/admin_art.go — same routes, same JSON shapes (camelCase
keys), same defaults. All routes require the `role: admin` JWT claim or a
Django staff session, enforced via the IsAdminToken permission class.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import IsAdminToken
from apps.studio.models import LLMJob, LLMRequestLog
from apps.studio.services.model_catalog import all_models, get_model
from apps.studio.settings_keys import (
    ALLOWED_LLM_MODELS,
    SETTING_LLM_DAILY_LIMIT,
    SETTING_LLM_ENABLED,
    SETTING_LLM_FLASH_MODEL,
    SETTING_LLM_MODEL,
    SETTING_LLM_PRO_MODEL,
    allowed_flash_models,
    allowed_pro_models,
    effective_daily_limit,
    effective_flash_model,
    effective_model,
    effective_pro_model,
    llm_enabled,
    set_allowed_flash_models,
    set_allowed_pro_models,
    set_setting,
)

from . import services
from .serializers import (
    ArtAdjustQuotaSerializer,
    ArtSetQuotaSerializer,
    ArtSettingsUpdateSerializer,
)
from django.conf import settings as dj_settings


def _iso_or_blank(dt) -> str:
    if not dt:
        return ""
    if not isinstance(dt, datetime):
        return ""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _settings_payload() -> dict:
    catalog = [
        {"id": m.id, "label": m.label, "role": m.role, "provider": m.provider}
        for m in all_models()
    ]
    return {
        "dailyLimit": effective_daily_limit(),
        "enabled": llm_enabled(),
        "configured": bool(dj_settings.DEEPSEEK_API_KEY or dj_settings.OPENCODE_API_KEY),
        "deepseekConfigured": bool(dj_settings.DEEPSEEK_API_KEY),
        "opencodeConfigured": bool(dj_settings.OPENCODE_API_KEY),
        # Legacy fields — admin UI built for the old single-model surface still works.
        "model": effective_pro_model(),
        "models": list(ALLOWED_LLM_MODELS),
        "baseUrl": dj_settings.DEEPSEEK_BASE_URL,
        # New fields.
        "catalog": catalog,
        "flashModel": effective_flash_model(),
        "proModel": effective_pro_model(),
        "allowedFlashModels": allowed_flash_models(),
        "allowedProModels": allowed_pro_models(),
    }


class AdminArtViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminToken]

    @action(detail=False, methods=["get"], url_path="users")
    def list_users(self, _request):
        limit = effective_daily_limit()
        users, date = services.list_art_users(limit)
        return Response({"users": users, "limit": limit, "date": date})

    @action(
        detail=False,
        methods=["put", "delete"],
        url_path=r"users/(?P<user_id>[^/]+)/quota",
    )
    def user_quota(self, request, user_id: str | None = None):
        if request.method == "DELETE":
            services.reset_user_quota_today(user_id)
            return Response({"message": "reset"})
        serializer = ArtSetQuotaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        count = services.set_user_quota_today(user_id, serializer.validated_data["count"])
        limit = effective_daily_limit()
        return Response({"count": count, "remaining": max(limit - count, 0), "limit": limit})

    @action(detail=False, methods=["post"], url_path=r"users/(?P<user_id>[^/]+)/quota/adjust")
    def adjust_user_quota(self, request, user_id: str | None = None):
        serializer = ArtAdjustQuotaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        count = services.adjust_user_quota_today(user_id, serializer.validated_data["delta"])
        limit = effective_daily_limit()
        return Response({"count": count, "remaining": max(limit - count, 0), "limit": limit})

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, _request):
        total_today, users_today_hit, total_7d, users_7d = services.art_stats()
        from apps.core.tz import local_today_str

        return Response(
            {
                "date": local_today_str(),
                "totalToday": total_today,
                "usersTodayHit": users_today_hit,
                "total7d": total_7d,
                "usersActive7d": users_7d,
                "limit": effective_daily_limit(),
                "enabled": llm_enabled(),
                "configured": bool(dj_settings.DEEPSEEK_API_KEY),
            }
        )

    @action(detail=False, methods=["get", "put"], url_path="settings")
    def settings_endpoint(self, request):
        if request.method == "GET":
            return Response(_settings_payload())
        serializer = ArtSettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "dailyLimit" in data:
            set_setting(SETTING_LLM_DAILY_LIMIT, str(data["dailyLimit"]))
        if "enabled" in data:
            set_setting(SETTING_LLM_ENABLED, "on" if data["enabled"] else "off")
        if "model" in data:
            # Legacy field — write to both legacy and new pro_model so the new
            # readers pick it up.
            set_setting(SETTING_LLM_MODEL, data["model"])
            set_setting(SETTING_LLM_PRO_MODEL, data["model"])
        if "proModel" in data:
            set_setting(SETTING_LLM_PRO_MODEL, data["proModel"])
        if "flashModel" in data:
            set_setting(SETTING_LLM_FLASH_MODEL, data["flashModel"])
        if "allowedProModels" in data:
            set_allowed_pro_models(data["allowedProModels"])
        if "allowedFlashModels" in data:
            set_allowed_flash_models(data["allowedFlashModels"])
        return Response(_settings_payload())

    # ─── DeepSeek call logs ────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="logs")
    def list_logs(self, request):
        params = request.query_params
        limit = max(min(int(params.get("limit") or 50), 200), 1)
        offset = max(int(params.get("offset") or 0), 0)
        qs = LLMRequestLog.objects.all()
        if params.get("user_id"):
            qs = qs.filter(user_id=params["user_id"])
        if params.get("mode"):
            qs = qs.filter(mode=params["mode"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        total = qs.count()
        rows = list(qs.order_by("-id")[offset : offset + limit])
        items = [
            {
                "id": r.id,
                "userId": r.user_id,
                "username": r.username,
                "mode": r.mode,
                "model": r.model,
                "attempt": r.attempt,
                "temperature": float(r.temperature) if isinstance(r.temperature, Decimal) else r.temperature,
                "status": r.status,
                "errorMessage": r.error_message or "",
                "latencyMs": r.latency_ms,
                "promptTokens": r.prompt_tokens,
                "completionTokens": r.completion_tokens,
                "totalTokens": r.total_tokens,
                "createdAt": _iso_or_blank(r.created_at),
            }
            for r in rows
        ]
        return Response({"items": items, "total": total, "limit": limit, "offset": offset})

    @action(detail=False, methods=["get"], url_path=r"logs/(?P<log_id>\d+)")
    def get_log(self, _request, log_id: str | None = None):
        row = LLMRequestLog.objects.filter(id=log_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "id": row.id,
                "userId": row.user_id,
                "username": row.username,
                "mode": row.mode,
                "model": row.model,
                "attempt": row.attempt,
                "temperature": float(row.temperature) if isinstance(row.temperature, Decimal) else row.temperature,
                "status": row.status,
                "errorMessage": row.error_message or "",
                "latencyMs": row.latency_ms,
                "promptTokens": row.prompt_tokens,
                "completionTokens": row.completion_tokens,
                "totalTokens": row.total_tokens,
                "createdAt": _iso_or_blank(row.created_at),
                "requestPayload": row.request_payload,
                "responseRaw": row.response_raw or "",
                "parsedScene": row.parsed_direction,
            }
        )

    # ─── Async job queue ────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="jobs")
    def list_jobs(self, request):
        params = request.query_params
        limit = max(min(int(params.get("limit") or 50), 200), 1)
        offset = max(int(params.get("offset") or 0), 0)
        qs = LLMJob.objects.all()
        if params.get("user_id"):
            qs = qs.filter(user_id=params["user_id"])
        if params.get("mode"):
            qs = qs.filter(mode=params["mode"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        total = qs.count()
        rows = list(qs.order_by("-created_at")[offset : offset + limit])
        items = []
        for r in rows:
            run_ms = 0
            if r.started_at and r.finished_at:
                run_ms = max(int((r.finished_at - r.started_at).total_seconds() * 1000), 0)
            items.append(
                {
                    "id": r.id,
                    "userId": r.user_id,
                    "username": r.username,
                    "mode": r.mode,
                    "status": r.status,
                    "attempt": r.attempt,
                    "errorMessage": r.error_message or "",
                    "runMs": run_ms,
                    "createdAt": _iso_or_blank(r.created_at),
                    "startedAt": _iso_or_blank(r.started_at),
                    "finishedAt": _iso_or_blank(r.finished_at),
                }
            )
        return Response({"items": items, "total": total, "limit": limit, "offset": offset})

    @action(detail=False, methods=["get"], url_path=r"jobs/(?P<job_id>[^/]+)")
    def get_job_detail(self, _request, job_id: str | None = None):
        r = LLMJob.objects.filter(id=job_id).first()
        if r is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        run_ms = 0
        if r.started_at and r.finished_at:
            run_ms = max(int((r.finished_at - r.started_at).total_seconds() * 1000), 0)
        return Response(
            {
                "id": r.id,
                "userId": r.user_id,
                "username": r.username,
                "mode": r.mode,
                "status": r.status,
                "attempt": r.attempt,
                "errorMessage": r.error_message or "",
                "runMs": run_ms,
                "createdAt": _iso_or_blank(r.created_at),
                "startedAt": _iso_or_blank(r.started_at),
                "finishedAt": _iso_or_blank(r.finished_at),
                "requestBody": r.request_body,
                "resultScene": r.result_scene,
                "resultMessage": r.result_message or "",
            }
        )
