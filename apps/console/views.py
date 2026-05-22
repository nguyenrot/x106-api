"""ViewSets for /api/v1/admin/console/.

Endpoints (all `IsAdminToken`):
- /sessions               CRUD-lite (list / create / get / delete)
- /sessions/{id}/messages POST — send a natural-language prompt to agy
- /messages/{id}          GET   — poll until status leaves pending/streaming
- /messages/{id}/retry    POST  — revive a failed assistant turn
- /settings               GET / PUT — enabled + system_prompt
"""

from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminToken

from .models import ConsoleMessage, ConsoleSession
from .serializers import (
    ConsoleSettingsSerializer,
    MessageSerializer,
    SendMessageSerializer,
    SessionDetailSerializer,
    SessionSerializer,
)
from .services.settings import get_bool, get_setting, set_setting
from .settings_keys import SETTING_ENABLED, SETTING_SYSTEM_PROMPT
from .tasks import run_console_chat

logger = logging.getLogger("x106.console.views")


def _require_enabled():
    if not get_bool(SETTING_ENABLED):
        raise PermissionDenied("VPS console is currently disabled.")


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
        content = payload.validated_data["content"]

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
            msg = ConsoleMessage.objects.select_related("session").get(
                pk=message_id, session__user=request.user
            )
        except ConsoleMessage.DoesNotExist as err:
            raise NotFound("message not found") from err
        return Response(MessageSerializer(msg).data)


class MessageRetryView(APIView):
    """POST /messages/{id}/retry — revive a failed assistant turn."""

    permission_classes = [IsAdminToken]

    def post(self, request, message_id: str):
        _require_enabled()
        try:
            msg = ConsoleMessage.objects.select_related("session").get(
                pk=message_id, session__user=request.user
            )
        except ConsoleMessage.DoesNotExist as err:
            raise NotFound("message not found") from err
        if msg.role != ConsoleMessage.ROLE_ASSISTANT:
            raise ValidationError({"role": "only assistant messages can be retried"})
        if msg.status != ConsoleMessage.STATUS_FAILED:
            raise ValidationError({"status": f"message is {msg.status}, can only retry failed"})

        updated = ConsoleMessage.objects.filter(
            pk=msg.pk, status=ConsoleMessage.STATUS_FAILED
        ).update(
            status=ConsoleMessage.STATUS_PENDING,
            error_message="",
            created_at=timezone.now(),
        )
        if not updated:
            raise ValidationError({"status": "message status changed; refresh"})
        run_console_chat.delay(msg.pk)
        return Response({"status": ConsoleMessage.STATUS_PENDING})


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
        return Response(_settings_payload())


def _settings_payload() -> dict:
    return {
        "enabled": get_bool(SETTING_ENABLED),
        "system_prompt": get_setting(SETTING_SYSTEM_PROMPT),
    }
