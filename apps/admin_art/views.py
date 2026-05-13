"""Admin endpoints for the AI art studio.

Mirrors internal/handler/admin_art.go — same routes, same JSON shapes (camelCase
keys), same defaults. All routes require the `role: admin` JWT claim or a
Django staff session, enforced via the IsAdminToken permission class.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from django.conf import settings as dj_settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import IsAdminToken
from apps.studio import quota as studio_quota
from apps.studio.models import (
    AppSettingChange,
    LLMJob,
    LLMJobStatus,
    LLMModel,
    LLMPromptKind,
    LLMPromptVersion,
    LLMRequestLog,
)
from apps.studio.services.model_catalog import (
    all_models,
    all_models_full,
)
from apps.studio.services.model_catalog import (
    clear_cache as clear_model_cache,
)
from apps.studio.settings_keys import (
    ALLOWED_LLM_MODELS,
    SETTING_LLM_DAILY_LIMIT,
    SETTING_LLM_ENABLED,
    SETTING_LLM_FLASH_MAX_TOKENS,
    SETTING_LLM_FLASH_MODEL,
    SETTING_LLM_MODEL,
    SETTING_LLM_PRO_MAX_TOKENS,
    SETTING_LLM_PRO_MODEL,
    allowed_flash_models,
    allowed_pro_models,
    effective_daily_limit,
    effective_flash_max_tokens,
    effective_flash_model,
    effective_pro_max_tokens,
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


def _iso_or_blank(dt) -> str:
    if not dt:
        return ""
    if not isinstance(dt, datetime):
        return ""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        "proMaxTokens": effective_pro_max_tokens(),
        "flashMaxTokens": effective_flash_max_tokens(),
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
        who = _admin_label(request)
        # Phase 3.6 — every set_setting carries changed_by so the audit table
        # records who flipped each value.
        if "dailyLimit" in data:
            set_setting(SETTING_LLM_DAILY_LIMIT, str(data["dailyLimit"]), changed_by=who)
        if "enabled" in data:
            set_setting(SETTING_LLM_ENABLED, "on" if data["enabled"] else "off", changed_by=who)
        if "model" in data:
            # Legacy field — write to both legacy and new pro_model so the new
            # readers pick it up.
            set_setting(SETTING_LLM_MODEL, data["model"], changed_by=who)
            set_setting(SETTING_LLM_PRO_MODEL, data["model"], changed_by=who)
        if "proModel" in data:
            set_setting(SETTING_LLM_PRO_MODEL, data["proModel"], changed_by=who)
        if "flashModel" in data:
            set_setting(SETTING_LLM_FLASH_MODEL, data["flashModel"], changed_by=who)
        if "allowedProModels" in data:
            set_allowed_pro_models(data["allowedProModels"])
        if "allowedFlashModels" in data:
            set_allowed_flash_models(data["allowedFlashModels"])
        if "proMaxTokens" in data:
            set_setting(SETTING_LLM_PRO_MAX_TOKENS, str(data["proMaxTokens"]), changed_by=who)
        if "flashMaxTokens" in data:
            set_setting(SETTING_LLM_FLASH_MAX_TOKENS, str(data["flashMaxTokens"]), changed_by=who)
        return Response(_settings_payload())

    @action(detail=False, methods=["get"], url_path="stats/timeseries")
    def stats_timeseries(self, request):
        """Phase 3.3 — bucketed counts + latency P50/P95 + cost per model.

        Query params:
          window: 24h | 7d | 30d (default 24h)
          bucket: hour | day (default: hour for 24h, day otherwise)
          group_by: model | status (default model)

        Real percentile via SQL window function — exact, not approximated.
        For small bucket × group cardinalities (≤ 50 × 24 ≈ 1200 rows) this
        is fast even on the prod read-replica."""
        from django.db import connection
        window = request.query_params.get("window", "24h")
        if window == "7d":
            interval_sql = "INTERVAL 7 DAY"
            default_bucket = "day"
        elif window == "30d":
            interval_sql = "INTERVAL 30 DAY"
            default_bucket = "day"
        else:
            interval_sql = "INTERVAL 24 HOUR"
            default_bucket = "hour"
        bucket = request.query_params.get("bucket", default_bucket)
        if bucket not in ("hour", "day"):
            bucket = default_bucket
        group_by = request.query_params.get("group_by", "model")
        if group_by not in ("model", "status"):
            group_by = "model"

        bucket_fmt = "%Y-%m-%dT%H:00:00Z" if bucket == "hour" else "%Y-%m-%dT00:00:00Z"
        sql = f"""
            WITH ranked AS (
                SELECT
                    DATE_FORMAT(created_at, '{bucket_fmt}') AS bucket_ts,
                    {group_by} AS dim,
                    latency_ms,
                    cost_cents,
                    status,
                    ROW_NUMBER() OVER (
                        PARTITION BY DATE_FORMAT(created_at, '{bucket_fmt}'), {group_by}
                        ORDER BY latency_ms
                    ) AS rn,
                    COUNT(*) OVER (
                        PARTITION BY DATE_FORMAT(created_at, '{bucket_fmt}'), {group_by}
                    ) AS total
                FROM llm_request_logs
                WHERE created_at > NOW() - {interval_sql}
            )
            SELECT
                bucket_ts,
                dim,
                COUNT(*) AS cnt,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                ROUND(AVG(latency_ms)) AS avg_ms,
                MAX(CASE WHEN rn = GREATEST(CEIL(total*0.5), 1) THEN latency_ms END) AS p50_ms,
                MAX(CASE WHEN rn = GREATEST(CEIL(total*0.95), 1) THEN latency_ms END) AS p95_ms,
                MAX(CASE WHEN rn = GREATEST(CEIL(total*0.99), 1) THEN latency_ms END) AS p99_ms,
                COALESCE(SUM(cost_cents), 0) AS cost
            FROM ranked
            GROUP BY bucket_ts, dim
            ORDER BY bucket_ts ASC, cnt DESC
        """
        # Bucket map: { ts → { dim_value: stats } }
        buckets: dict[str, dict] = {}
        try:
            with connection.cursor() as cur:
                cur.execute(sql)
                for ts, dim, cnt, ok, avg_ms, p50, p95, p99, cost in cur.fetchall():
                    b = buckets.setdefault(ts, {"ts": ts, "count": 0, "successCount": 0, "totalCostCents": 0, "byKey": {}})
                    b["count"] += int(cnt or 0)
                    b["successCount"] += int(ok or 0)
                    b["totalCostCents"] += int(cost or 0)
                    b["byKey"][dim or "unknown"] = {
                        "count": int(cnt or 0),
                        "successCount": int(ok or 0),
                        "avgMs": int(avg_ms or 0),
                        "p50Ms": int(p50 or 0),
                        "p95Ms": int(p95 or 0),
                        "p99Ms": int(p99 or 0),
                        "totalCostCents": int(cost or 0),
                    }
        except Exception as exc:  # noqa: BLE001
            return Response({"error": str(exc), "buckets": []}, status=500)

        # Also compute aggregate latency per bucket (across all dims) — useful
        # for the requests/hour overview chart.
        sql_agg = f"""
            WITH ranked2 AS (
                SELECT
                    DATE_FORMAT(created_at, '{bucket_fmt}') AS bucket_ts,
                    latency_ms,
                    ROW_NUMBER() OVER (
                        PARTITION BY DATE_FORMAT(created_at, '{bucket_fmt}')
                        ORDER BY latency_ms
                    ) AS rn,
                    COUNT(*) OVER (
                        PARTITION BY DATE_FORMAT(created_at, '{bucket_fmt}')
                    ) AS total
                FROM llm_request_logs
                WHERE created_at > NOW() - {interval_sql}
            )
            SELECT
                bucket_ts,
                MAX(CASE WHEN rn = GREATEST(CEIL(total*0.5), 1) THEN latency_ms END) AS p50,
                MAX(CASE WHEN rn = GREATEST(CEIL(total*0.95), 1) THEN latency_ms END) AS p95
            FROM ranked2
            GROUP BY bucket_ts
        """
        try:
            with connection.cursor() as cur:
                cur.execute(sql_agg)
                for ts, p50, p95 in cur.fetchall():
                    if ts in buckets:
                        buckets[ts]["p50Ms"] = int(p50 or 0)
                        buckets[ts]["p95Ms"] = int(p95 or 0)
        except Exception:  # noqa: BLE001
            pass

        return Response({
            "window": window,
            "bucket": bucket,
            "groupBy": group_by,
            "buckets": [buckets[k] for k in sorted(buckets.keys())],
        })

    @action(detail=False, methods=["get"], url_path="settings/history")
    def settings_history(self, request):
        """Phase 3.6 — paginated audit log. Filter by setting_name + window."""
        params = request.query_params
        limit = max(min(int(params.get("limit") or 50), 200), 1)
        qs = AppSettingChange.objects.all()
        if params.get("key"):
            qs = qs.filter(setting_name=params["key"])
        if params.get("since"):
            qs = qs.filter(changed_at__gte=params["since"])
        rows = list(qs.order_by("-changed_at")[:limit])
        items = [
            {
                "id": r.id,
                "settingName": r.setting_name,
                "oldValue": r.old_value or "",
                "newValue": r.new_value or "",
                "changedBy": r.changed_by or "",
                "changedAt": _iso_or_blank(r.changed_at),
            }
            for r in rows
        ]
        return Response({"items": items})

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
        # Phase 3.5 — ISO 8601 since/until window. Both optional.
        if params.get("since"):
            qs = qs.filter(created_at__gte=params["since"])
        if params.get("until"):
            qs = qs.filter(created_at__lte=params["until"])
        # Phase 3.4 — content search via FULLTEXT virtual column (added in
        # migration 0012). Only fires for queries ≥ 3 chars to dodge MySQL
        # ft_min_token_size default (4); shorter queries fall back to LIKE.
        q_term = (params.get("q") or "").strip()
        if q_term:
            if len(q_term) >= 4:
                qs = qs.extra(
                    where=["MATCH(user_message_text) AGAINST (%s IN NATURAL LANGUAGE MODE)"],
                    params=[q_term],
                )
            else:
                qs = qs.filter(user_message_text__icontains=q_term)
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
        # Phase 3.5 — ISO 8601 since/until window. Both optional.
        if params.get("since"):
            qs = qs.filter(created_at__gte=params["since"])
        if params.get("until"):
            qs = qs.filter(created_at__lte=params["until"])
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

    # ─── Phase 4 model management ─────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="models")
    def list_models(self, request):
        """Full catalog including disabled/deprecated. Used by admin
        ArtModelsTab. Stats overlay (7d) joined from llm_request_logs."""
        include_disabled = request.query_params.get("include_disabled", "true").lower() != "false"
        # Cache-bust on read so admin sees fresh data after a mutation.
        clear_model_cache()
        rows = all_models_full()
        if not include_disabled:
            rows = [r for r in rows if r["enabled"]]

        stats_by_model = _model_stats_7d()
        out = [_model_payload(r, stats_by_model.get(r["slug"])) for r in rows]
        return Response({"models": out})

    @action(detail=False, methods=["post"], url_path="models")
    def create_model(self, request):
        data = request.data or {}
        slug = (data.get("slug") or "").strip()
        if not slug:
            return Response({"error": "slug required"}, status=status.HTTP_400_BAD_REQUEST)
        if LLMModel.objects.filter(slug=slug).exists():
            return Response({"error": "slug already exists"}, status=status.HTTP_409_CONFLICT)
        try:
            row = LLMModel.objects.create(
                slug=slug,
                display_name=(data.get("displayName") or slug)[:80],
                provider=data.get("provider") or "deepseek",
                remote_id=(data.get("remoteId") or slug)[:120],
                role=data.get("role") or "pro",
                description=(data.get("description") or "")[:240],
                speed_badge=(data.get("speedBadge") or "")[:16],
                quality_badge=(data.get("qualityBadge") or "")[:16],
                cost_badge=(data.get("costBadge") or "")[:16],
                enabled=bool(data.get("enabled", True)),
                allowed_for_users=bool(data.get("allowedForUsers", False)),
                beta=bool(data.get("beta", False)),
                deprecated=bool(data.get("deprecated", False)),
                prompt_cents_per_mtok=data.get("promptCentsPerMtok"),
                completion_cents_per_mtok=data.get("completionCentsPerMtok"),
                max_tokens_override=data.get("maxTokensOverride"),
                sort_order=int(data.get("sortOrder") or 100),
            )
        except Exception as exc:  # noqa: BLE001 — surface as 400 not 500
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        clear_model_cache()
        return Response(_model_payload(_row_dict(row)), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["put", "patch"], url_path=r"models/(?P<model_id>[^/]+)")
    def update_model(self, request, model_id: str | None = None):
        row = LLMModel.objects.filter(slug=model_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        data = request.data or {}
        # Map camelCase request fields → model attributes. Only update fields
        # that are present (PATCH semantics).
        field_map = {
            "displayName": ("display_name", lambda v: (str(v) or "")[:80]),
            "provider": ("provider", lambda v: str(v)),
            "remoteId": ("remote_id", lambda v: (str(v) or "")[:120]),
            "role": ("role", lambda v: str(v)),
            "description": ("description", lambda v: (str(v) or "")[:240]),
            "speedBadge": ("speed_badge", lambda v: (str(v) or "")[:16]),
            "qualityBadge": ("quality_badge", lambda v: (str(v) or "")[:16]),
            "costBadge": ("cost_badge", lambda v: (str(v) or "")[:16]),
            "enabled": ("enabled", bool),
            "allowedForUsers": ("allowed_for_users", bool),
            "beta": ("beta", bool),
            "deprecated": ("deprecated", bool),
            "promptCentsPerMtok": ("prompt_cents_per_mtok", lambda v: None if v in (None, "") else int(v)),
            "completionCentsPerMtok": ("completion_cents_per_mtok", lambda v: None if v in (None, "") else int(v)),
            "maxTokensOverride": ("max_tokens_override", lambda v: None if v in (None, "") else int(v)),
            "sortOrder": ("sort_order", lambda v: int(v)),
        }
        for key, (attr, coerce) in field_map.items():
            if key in data:
                try:
                    setattr(row, attr, coerce(data[key]))
                except (TypeError, ValueError) as exc:
                    return Response(
                        {"error": f"invalid {key}: {exc}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        row.save()
        clear_model_cache()
        return Response(_model_payload(_row_dict(row)))

    @action(detail=False, methods=["post"], url_path=r"models/(?P<model_id>[^/]+)/toggle-enabled")
    def toggle_enabled(self, _request, model_id: str | None = None):
        row = LLMModel.objects.filter(slug=model_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        row.enabled = not row.enabled
        row.save(update_fields=["enabled", "updated_at"])
        clear_model_cache()
        return Response({"slug": row.slug, "enabled": row.enabled})

    @action(detail=False, methods=["post"], url_path=r"models/(?P<model_id>[^/]+)/toggle-allowed")
    def toggle_allowed(self, _request, model_id: str | None = None):
        row = LLMModel.objects.filter(slug=model_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        row.allowed_for_users = not row.allowed_for_users
        row.save(update_fields=["allowed_for_users", "updated_at"])
        clear_model_cache()
        return Response({"slug": row.slug, "allowedForUsers": row.allowed_for_users})

    @action(detail=False, methods=["post"], url_path=r"models/(?P<model_id>[^/]+)/set-default")
    def set_default(self, request, model_id: str | None = None):
        """Set this model as the default for `role` (pro or flash). Mutex
        across rows — the previous default is cleared in the same transaction."""
        from django.db import transaction

        row = LLMModel.objects.filter(slug=model_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        role = (request.data.get("role") or "").strip()
        if role not in ("pro", "flash"):
            return Response({"error": "role must be 'pro' or 'flash'"}, status=status.HTTP_400_BAD_REQUEST)
        if role == "pro" and row.role not in ("pro", "both"):
            return Response(
                {"error": "model does not support role=pro"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if role == "flash" and row.role not in ("flash", "both"):
            return Response(
                {"error": "model does not support role=flash"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not row.enabled:
            return Response(
                {"error": "model is disabled; enable it before setting as default"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            if role == "pro":
                LLMModel.objects.filter(is_default_pro=True).update(is_default_pro=False)
                LLMModel.objects.filter(slug=model_id).update(is_default_pro=True)
            else:
                LLMModel.objects.filter(is_default_flash=True).update(is_default_flash=False)
                LLMModel.objects.filter(slug=model_id).update(is_default_flash=True)
        clear_model_cache()
        return Response({"slug": model_id, "role": role, "isDefault": True})

    @action(detail=False, methods=["delete"], url_path=r"models/(?P<model_id>[^/]+)")
    def delete_model(self, _request, model_id: str | None = None):
        row = LLMModel.objects.filter(slug=model_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if row.is_default_pro or row.is_default_flash:
            return Response(
                {"error": "cannot delete a default model; set a different default first"},
                status=status.HTTP_409_CONFLICT,
            )
        # Hard delete only if no logs reference this model. Otherwise soft-delete
        # (enabled=false + deprecated=true) to preserve audit trail.
        has_logs = LLMRequestLog.objects.filter(model=row.slug).exists()
        if has_logs:
            row.enabled = False
            row.deprecated = True
            row.allowed_for_users = False
            row.save(update_fields=["enabled", "deprecated", "allowed_for_users", "updated_at"])
            clear_model_cache()
            return Response({"slug": row.slug, "softDeleted": True})
        row.delete()
        clear_model_cache()
        return Response({"slug": model_id, "softDeleted": False})

    # ─── Phase 3.2 job control ────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path=r"jobs/(?P<job_id>[^/]+)/cancel")
    def admin_cancel_job(self, _request, job_id: str | None = None):
        """Admin cancel — same code path as the user-facing cancel (revoke +
        conditional UPDATE) but bypasses user_id ownership check so an operator
        can stop any stuck job."""
        job = LLMJob.objects.filter(id=job_id).first()
        if job is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if job.status == LLMJobStatus.PENDING:
            updated = LLMJob.objects.filter(id=job_id, status=LLMJobStatus.PENDING).update(
                status=LLMJobStatus.CANCELED,
                finished_at=timezone.now(),
                error_message="canceled by admin",
            )
            if updated:
                studio_quota.refund(job.user_id)
            return Response(
                {"jobId": job.id, "status": LLMJobStatus.CANCELED, "refunded": bool(updated)}
            )
        if job.status == LLMJobStatus.PROCESSING:
            updated = LLMJob.objects.filter(
                id=job_id, status=LLMJobStatus.PROCESSING,
            ).update(
                status=LLMJobStatus.CANCELED,
                finished_at=timezone.now(),
                error_message="canceled by admin",
            )
            refunded = False
            if updated and job.celery_task_id:
                try:
                    from celery import current_app
                    current_app.control.revoke(
                        job.celery_task_id, terminate=True, signal="SIGTERM"
                    )
                except Exception:  # noqa: BLE001
                    pass
                studio_quota.refund(job.user_id)
                refunded = True
            return Response(
                {"jobId": job.id, "status": LLMJobStatus.CANCELED, "refunded": refunded}
            )
        return Response(
            {"jobId": job.id, "status": job.status, "refunded": False, "noop": True}
        )

    @action(detail=False, methods=["post"], url_path=r"jobs/(?P<job_id>[^/]+)/retry")
    def admin_retry_job(self, _request, job_id: str | None = None):
        """Clone a terminal job into a fresh pending row + enqueue. Admin debug
        retries don't charge the user's quota — set bypass via direct create
        without quota.reserve(). Old row keeps its audit trail intact."""
        from apps.core.ids import new_id
        from apps.studio.tasks import run_llm_job

        old = LLMJob.objects.filter(id=job_id).first()
        if old is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if old.status not in (LLMJobStatus.FAILED, LLMJobStatus.CANCELED):
            return Response(
                {"error": "can only retry failed/canceled jobs"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_job = LLMJob.objects.create(
            id=new_id(),
            user_id=old.user_id,
            username=old.username,
            mode=old.mode,
            request_body=old.request_body,
            pro_model=old.pro_model,
            flash_model=old.flash_model,
        )
        async_result = run_llm_job.delay(new_job.id)
        LLMJob.objects.filter(id=new_job.id).update(celery_task_id=async_result.id)
        return Response(
            {"jobId": new_job.id, "clonedFrom": old.id, "status": LLMJobStatus.PENDING},
            status=status.HTTP_202_ACCEPTED,
        )

    # ─── Phase 3.1 prompt version management ──────────────────────────────

    @action(detail=False, methods=["get"], url_path="prompt-versions")
    def list_prompt_versions(self, request):
        kind = request.query_params.get("kind", "").strip()
        qs = LLMPromptVersion.objects.all()
        if kind in (LLMPromptKind.CHAT, LLMPromptKind.ROUTER):
            qs = qs.filter(kind=kind)
        limit = max(min(int(request.query_params.get("limit") or 50), 200), 1)
        rows = list(qs.order_by("-created_at")[:limit])
        return Response({
            "items": [_prompt_payload(r, body_preview=True) for r in rows],
        })

    @action(detail=False, methods=["get"], url_path=r"prompt-versions/(?P<pv_id>\d+)")
    def get_prompt_version(self, _request, pv_id: str | None = None):
        row = LLMPromptVersion.objects.filter(id=pv_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(_prompt_payload(row, body_preview=False))

    @action(detail=False, methods=["post"], url_path="prompt-versions")
    def create_prompt_version(self, request):
        """Create a new prompt version. If activate=true, deactivates other
        rows of the same kind (mutex per kind enforced by partial unique
        constraint at DB level + explicit deactivate inside transaction)."""
        from django.db import transaction

        from apps.studio.services.prompts import clear_prompt_cache

        data = request.data or {}
        kind = (data.get("kind") or "").strip()
        body = data.get("body") or ""
        if kind not in (LLMPromptKind.CHAT, LLMPromptKind.ROUTER):
            return Response({"error": "kind must be chat or router"}, status=status.HTTP_400_BAD_REQUEST)
        if not body.strip():
            return Response({"error": "body required"}, status=status.HTTP_400_BAD_REQUEST)
        notes = (data.get("notes") or "")[:240]
        activate = bool(data.get("activate", False))
        created_by = _admin_label(request)[:64]

        with transaction.atomic():
            if activate:
                LLMPromptVersion.objects.filter(kind=kind, is_active=True).update(is_active=False)
            row = LLMPromptVersion.objects.create(
                kind=kind, body=body, notes=notes,
                created_by=created_by, is_active=activate,
            )
        clear_prompt_cache()
        return Response(_prompt_payload(row, body_preview=False), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path=r"prompt-versions/(?P<pv_id>\d+)/activate")
    def activate_prompt_version(self, _request, pv_id: str | None = None):
        from django.db import transaction

        from apps.studio.services.prompts import clear_prompt_cache

        row = LLMPromptVersion.objects.filter(id=pv_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            LLMPromptVersion.objects.filter(kind=row.kind, is_active=True).update(is_active=False)
            LLMPromptVersion.objects.filter(id=pv_id).update(is_active=True)
        clear_prompt_cache()
        return Response({"id": row.id, "kind": row.kind, "isActive": True})

    @action(detail=False, methods=["delete"], url_path=r"prompt-versions/(?P<pv_id>\d+)")
    def delete_prompt_version(self, _request, pv_id: str | None = None):
        row = LLMPromptVersion.objects.filter(id=pv_id).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        if row.is_active:
            return Response(
                {"error": "cannot delete active version; activate a different one first"},
                status=status.HTTP_409_CONFLICT,
            )
        # FK check — refuse delete if any log references this version.
        if LLMRequestLog.objects.filter(prompt_version_id=row.id).exists():
            return Response(
                {"error": "log rows reference this version; cannot delete"},
                status=status.HTTP_409_CONFLICT,
            )
        row.delete()
        return Response({"id": int(pv_id), "deleted": True})


def _admin_label(request) -> str:
    """Best-effort 'who edited' for created_by audit. Falls back to '?' when
    the JWT payload doesn't carry a username (shouldn't happen for admins)."""
    user = getattr(request, "user", None)
    if user is None:
        return "?"
    return getattr(user, "username", None) or str(getattr(user, "id", "?"))


def _prompt_payload(row: LLMPromptVersion, body_preview: bool) -> dict:
    body = row.body
    if body_preview and len(body) > 200:
        body = body[:200] + "…"
    return {
        "id": row.id,
        "kind": row.kind,
        "body": body,
        "notes": row.notes or "",
        "createdBy": row.created_by or "",
        "createdAt": _iso_or_blank(row.created_at),
        "isActive": row.is_active,
    }


def _model_payload(row: dict, stats: dict | None = None) -> dict:
    """Project an LLMModel row dict to the camelCase admin response shape."""
    return {
        "slug": row["slug"],
        "displayName": row["display_name"],
        "provider": row["provider"],
        "remoteId": row["remote_id"],
        "role": row["role"],
        "description": row.get("description") or "",
        "badges": {
            "speed": row.get("speed_badge") or "",
            "quality": row.get("quality_badge") or "",
            "cost": row.get("cost_badge") or "",
        },
        "enabled": bool(row.get("enabled", True)),
        "isDefaultPro": bool(row.get("is_default_pro", False)),
        "isDefaultFlash": bool(row.get("is_default_flash", False)),
        "allowedForUsers": bool(row.get("allowed_for_users", False)),
        "deprecated": bool(row.get("deprecated", False)),
        "beta": bool(row.get("beta", False)),
        "promptCentsPerMtok": row.get("prompt_cents_per_mtok"),
        "completionCentsPerMtok": row.get("completion_cents_per_mtok"),
        "maxTokensOverride": row.get("max_tokens_override"),
        "stats7d": stats or {"count": 0, "successCount": 0, "p95Ms": 0, "totalCostCents": 0},
    }


def _row_dict(row: LLMModel) -> dict:
    """Map LLMModel instance → dict matching the cache projection."""
    return {
        "slug": row.slug,
        "display_name": row.display_name,
        "provider": row.provider,
        "remote_id": row.remote_id,
        "role": row.role,
        "description": row.description,
        "speed_badge": row.speed_badge,
        "quality_badge": row.quality_badge,
        "cost_badge": row.cost_badge,
        "enabled": row.enabled,
        "is_default_pro": row.is_default_pro,
        "is_default_flash": row.is_default_flash,
        "allowed_for_users": row.allowed_for_users,
        "deprecated": row.deprecated,
        "beta": row.beta,
        "prompt_cents_per_mtok": row.prompt_cents_per_mtok,
        "completion_cents_per_mtok": row.completion_cents_per_mtok,
        "max_tokens_override": row.max_tokens_override,
    }


def _model_stats_7d() -> dict[str, dict]:
    """Per-model rollup of last 7d: count, success_count, P95 latency, total cost.
    Cached at the admin response layer (~5min via Phase 3.3 endpoint when it
    lands); for now we recompute per list call which is acceptable given the
    small row volume."""
    from django.db import connection
    out: dict[str, dict] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT
                    model,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                    -- MySQL 8 percentile approximation; if available, replace with
                    -- PERCENTILE_CONT. For now use approximation via NTILE.
                    AVG(latency_ms) AS avg_latency,
                    MAX(latency_ms) AS max_latency,
                    COALESCE(SUM(cost_cents), 0) AS cost
                FROM llm_request_logs
                WHERE created_at > NOW() - INTERVAL 7 DAY
                GROUP BY model
                """
            )
            for model_id, total, ok, avg_lat, max_lat, cost in cur.fetchall():
                out[model_id] = {
                    "count": int(total or 0),
                    "successCount": int(ok or 0),
                    "p95Ms": int(max_lat or 0),  # approximation — replaced in Phase 3.3
                    "avgMs": int(avg_lat or 0),
                    "totalCostCents": int(cost or 0),
                }
    except Exception:  # noqa: BLE001
        # Stats are best-effort — never break the admin list endpoint.
        pass
    return out
