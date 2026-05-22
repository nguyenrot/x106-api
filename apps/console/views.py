"""ViewSets for /api/v1/admin/console/.

Endpoints (all `IsAdminToken`):
- /sessions               CRUD-lite (list / create / get / delete)
- /sessions/{id}/messages POST — send NL or direct command
- /messages/{id}          GET   — poll
- /execs/{id}             GET   — poll
- /execs/{id}/approve     POST  — green-light a pending shell call
- /execs/{id}/deny        POST  — reject + feed back into chat loop
- /execs/{id}/cancel      POST  — abort a running command
- /execs/{id}/explain     POST  — one-shot LLM summary of an exec's output
- /logs                   GET   — audit trail (paginated)
- /settings               GET / PUT — admin controls
"""

from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminToken

from .models import ConsoleExec, ConsoleMessage, ConsoleSession
from .serializers import (
    ApproveExecSerializer,
    ConsoleSettingsSerializer,
    DenyExecSerializer,
    ExecSerializer,
    MessageSerializer,
    SendMessageSerializer,
    SessionDetailSerializer,
    SessionSerializer,
)
from .services import llm as llm_service
from .services.danger import classify
from .services.settings import get_bool, get_int, get_setting, set_setting
from .settings_keys import (
    ALLOWED_MODELS,
    SETTING_AI_MODEL,
    SETTING_COMMAND_TIMEOUT_SEC,
    SETTING_DESTROY_PHRASE,
    SETTING_ENABLED,
    SETTING_MAX_AGENT_STEPS,
    SETTING_SYSTEM_PROMPT,
)
from .tasks import run_console_chat, run_console_exec

logger = logging.getLogger("x106.console.views")


def _require_enabled():
    if not get_bool(SETTING_ENABLED):
        raise PermissionDenied("VPS console is currently disabled.")


def _safety_check_for_direct(exec_row: ConsoleExec, destroy_phrase: str) -> None:
    """For `$ ` direct commands: safe/write auto-approve, destructive needs the
    phrase. Raises ValidationError when the phrase is wrong."""
    if exec_row.danger_level != ConsoleExec.DANGER_DESTRUCTIVE:
        return
    expected = get_setting(SETTING_DESTROY_PHRASE)
    if destroy_phrase.strip() != expected.strip():
        raise ValidationError(
            {"destroy_phrase": f"Type '{expected}' exactly to confirm a destructive command."}
        )


class SessionViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAdminToken]
    serializer_class = SessionSerializer
    lookup_field = "id"
    # Sessions list is small (single admin) and the frontend types it as a
    # flat array — opt out of the project's default LimitOffsetPagination.
    pagination_class = None

    def get_queryset(self):
        return ConsoleSession.objects.filter(user=self.request.user).order_by("-updated_at")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SessionDetailSerializer
        return SessionSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="messages")
    def messages(self, request, id=None):
        _require_enabled()
        session = self.get_object()

        payload = SendMessageSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        content = payload.validated_data.get("content")
        exec_command = payload.validated_data.get("exec_command")

        if exec_command:
            return self._send_direct(session, exec_command, request.data.get("destroy_phrase", ""))
        return self._send_chat(session, content)

    def _send_direct(self, session: ConsoleSession, command: str, destroy_phrase: str):
        level, reasons = classify(command)
        # Record the user's typed message as a `user` ConsoleMessage so the
        # session view shows it inline.
        user_msg = ConsoleMessage.objects.create(
            session=session,
            role=ConsoleMessage.ROLE_USER,
            content=f"$ {command}",
            status=ConsoleMessage.STATUS_DONE,
        )
        exec_row = ConsoleExec.objects.create(
            session=session,
            message=None,  # not part of an AI loop
            user=session.user,
            command=command,
            source=ConsoleExec.SOURCE_USER_DIRECT,
            danger_level=level,
            danger_reasons=reasons,
            status=ConsoleExec.STATUS_AWAITING_CONFIRM,
        )

        if level == ConsoleExec.DANGER_DESTRUCTIVE:
            _safety_check_for_direct(exec_row, destroy_phrase)

        # Auto-approve safe + write (level guard above handled destructive).
        ConsoleExec.objects.filter(pk=exec_row.pk, status=ConsoleExec.STATUS_AWAITING_CONFIRM).update(
            status=ConsoleExec.STATUS_APPROVED
        )
        run_console_exec.delay(exec_row.pk)
        ConsoleSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())

        exec_row.refresh_from_db()
        return Response(
            {
                "messageId": user_msg.pk,
                "execId": exec_row.pk,
                "status": exec_row.status,
                "dangerLevel": exec_row.danger_level,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _send_chat(self, session: ConsoleSession, content: str):
        ConsoleMessage.objects.create(
            session=session,
            role=ConsoleMessage.ROLE_USER,
            content=content,
            status=ConsoleMessage.STATUS_DONE,
        )
        assistant = ConsoleMessage.objects.create(
            session=session,
            role=ConsoleMessage.ROLE_ASSISTANT,
            content="",
            status=ConsoleMessage.STATUS_PENDING,
        )
        run_console_chat.delay(assistant.pk)
        ConsoleSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())
        return Response(
            {"messageId": assistant.pk, "status": assistant.status},
            status=status.HTTP_202_ACCEPTED,
        )


class MessagePollView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, request, message_id: str):
        try:
            msg = ConsoleMessage.objects.select_related("session").prefetch_related("execs").get(
                pk=message_id, session__user=request.user
            )
        except ConsoleMessage.DoesNotExist as err:
            raise NotFound("message not found") from err
        return Response(MessageSerializer(msg).data)


class ExecViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAdminToken]
    serializer_class = ExecSerializer
    lookup_field = "id"

    def get_queryset(self):
        return ConsoleExec.objects.filter(user=self.request.user)

    def retrieve(self, request, id=None):
        try:
            exec_row = self.get_queryset().get(pk=id)
        except ConsoleExec.DoesNotExist as err:
            raise NotFound("exec not found") from err
        return Response(ExecSerializer(exec_row).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, id=None):
        _require_enabled()
        body = ApproveExecSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            exec_row = self.get_queryset().get(pk=id)
        except ConsoleExec.DoesNotExist as err:
            raise NotFound("exec not found") from err
        if exec_row.status != ConsoleExec.STATUS_AWAITING_CONFIRM:
            raise ValidationError({"status": f"exec is {exec_row.status}, cannot approve"})
        if exec_row.danger_level == ConsoleExec.DANGER_DESTRUCTIVE:
            expected = get_setting(SETTING_DESTROY_PHRASE)
            if (body.validated_data.get("destroy_phrase") or "").strip() != expected.strip():
                raise ValidationError(
                    {"destroy_phrase": f"Type '{expected}' to confirm destructive command."}
                )

        updated = ConsoleExec.objects.filter(
            pk=exec_row.pk, status=ConsoleExec.STATUS_AWAITING_CONFIRM
        ).update(status=ConsoleExec.STATUS_APPROVED)
        if not updated:
            raise ValidationError({"status": "exec status changed; refresh"})
        run_console_exec.delay(exec_row.pk)
        return Response({"status": ConsoleExec.STATUS_APPROVED})

    @action(detail=True, methods=["post"])
    def deny(self, request, id=None):
        body = DenyExecSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            exec_row = self.get_queryset().get(pk=id)
        except ConsoleExec.DoesNotExist as err:
            raise NotFound("exec not found") from err
        if exec_row.status != ConsoleExec.STATUS_AWAITING_CONFIRM:
            raise ValidationError({"status": f"exec is {exec_row.status}, cannot deny"})

        updated = ConsoleExec.objects.filter(
            pk=exec_row.pk, status=ConsoleExec.STATUS_AWAITING_CONFIRM
        ).update(
            status=ConsoleExec.STATUS_DENIED,
            deny_reason=(body.validated_data.get("reason") or "")[:1000],
            finished_at=timezone.now(),
        )
        if not updated:
            raise ValidationError({"status": "exec status changed; refresh"})

        # If the AI proposed this, push the denial back into the loop so it can
        # respond / try a different approach.
        exec_row.refresh_from_db()
        if exec_row.message_id:
            from .tasks import _resume_chat_if_linked

            _resume_chat_if_linked(exec_row)
        return Response({"status": ConsoleExec.STATUS_DENIED})

    @action(detail=True, methods=["post"])
    def cancel(self, request, id=None):
        try:
            exec_row = self.get_queryset().get(pk=id)
        except ConsoleExec.DoesNotExist as err:
            raise NotFound("exec not found") from err
        if exec_row.status not in (
            ConsoleExec.STATUS_AWAITING_CONFIRM,
            ConsoleExec.STATUS_APPROVED,
            ConsoleExec.STATUS_RUNNING,
        ):
            raise ValidationError({"status": f"cannot cancel a {exec_row.status} exec"})
        ConsoleExec.objects.filter(pk=exec_row.pk, status=exec_row.status).update(
            status=ConsoleExec.STATUS_CANCELED, finished_at=timezone.now()
        )
        return Response({"status": ConsoleExec.STATUS_CANCELED})

    @action(detail=True, methods=["post"])
    def explain(self, request, id=None):
        _require_enabled()
        try:
            exec_row = self.get_queryset().get(pk=id)
        except ConsoleExec.DoesNotExist as err:
            raise NotFound("exec not found") from err
        if exec_row.status != ConsoleExec.STATUS_DONE:
            raise ValidationError({"status": "exec must be done to explain"})
        model = get_setting(SETTING_AI_MODEL)
        prompt = (
            f"Lệnh đã chạy: `{exec_row.command}`\n"
            f"Exit code: {exec_row.exit_code}\n"
            f"stdout:\n```\n{(exec_row.stdout or '')[:6000]}\n```\n"
            f"stderr:\n```\n{(exec_row.stderr or '')[:2000]}\n```\n\n"
            "Hãy tóm tắt ngắn output bằng tiếng Việt: trạng thái OK/cảnh báo/lỗi, "
            "ý nghĩa con số quan trọng, và một gợi ý hành động tiếp theo nếu cần."
        )
        try:
            result = llm_service.chat_completion(
                messages=[
                    {"role": "system", "content": "Bạn là kỹ sư DevOps giải thích output shell ngắn gọn bằng tiếng Việt."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
                temperature=0.2,
                max_tokens=500,
            )
        except llm_service.LLMConfigError as err:
            return Response({"error": str(err)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except llm_service.LLMTransportError as err:
            return Response({"error": str(err)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"summary": result.text})


class LogsView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, request):
        qs = ConsoleExec.objects.filter(user=request.user).order_by("-created_at")

        if cmd := request.query_params.get("command"):
            qs = qs.filter(command__icontains=cmd)
        if since := request.query_params.get("since"):
            qs = qs.filter(created_at__gte=since)
        if until := request.query_params.get("until"):
            qs = qs.filter(created_at__lt=until)
        if exit_code := request.query_params.get("exit_code"):
            try:
                qs = qs.filter(exit_code=int(exit_code))
            except ValueError as err:
                raise ValidationError({"exit_code": "must be int"}) from err
        if level := request.query_params.get("danger_level"):
            qs = qs.filter(danger_level=level)

        try:
            limit = max(1, min(int(request.query_params.get("limit", 50)), 200))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError as err:
            raise ValidationError("limit/offset must be int") from err

        total = qs.count()
        page = list(qs[offset : offset + limit])
        return Response(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": ExecSerializer(page, many=True).data,
            }
        )


class SettingsView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, request):
        return Response(_settings_payload())

    def put(self, request):
        ser = ConsoleSettingsSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        set_setting(SETTING_ENABLED, "true" if data["enabled"] else "false")
        set_setting(SETTING_SYSTEM_PROMPT, data["system_prompt"])
        set_setting(SETTING_AI_MODEL, data["ai_model"])
        set_setting(SETTING_COMMAND_TIMEOUT_SEC, str(data["command_timeout_sec"]))
        set_setting(SETTING_MAX_AGENT_STEPS, str(data["max_agent_steps"]))
        set_setting(SETTING_DESTROY_PHRASE, data["destroy_phrase"])
        return Response(_settings_payload())


def _settings_payload() -> dict:
    return {
        "enabled": get_bool(SETTING_ENABLED),
        "system_prompt": get_setting(SETTING_SYSTEM_PROMPT),
        "ai_model": get_setting(SETTING_AI_MODEL),
        "command_timeout_sec": get_int(SETTING_COMMAND_TIMEOUT_SEC),
        "max_agent_steps": get_int(SETTING_MAX_AGENT_STEPS),
        "destroy_phrase": get_setting(SETTING_DESTROY_PHRASE),
        "allowed_models": list(ALLOWED_MODELS),
    }
