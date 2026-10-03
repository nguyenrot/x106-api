"""/api/v1/admin/ops/ — the admin console's cockpit endpoints (all `IsAdminToken`).

- GET          /overview             host metrics + pm2 + systemd (cached 5s)
- GET          /terminals            tmux sessions of the admin Terminal
- PATCH|DELETE /terminals/{name}     rename (`{"name": "new"}`) / kill a session
- GET|PUT      /snippets             the console's saved command snippets
"""

from __future__ import annotations

from django.core.cache import cache
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminToken

from .models import OpsPref
from .services import host, tmux

OVERVIEW_CACHE_KEY = "ops:overview"
OVERVIEW_TTL_SEC = 5


class TmuxUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "tmux is unavailable."


class OverviewView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, _request):
        # Every open admin tab polls this; one /proc sample + pm2 + systemctl
        # per 5s per worker is plenty.
        return Response(cache.get_or_set(OVERVIEW_CACHE_KEY, host.overview, OVERVIEW_TTL_SEC))


class TerminalListView(APIView):
    permission_classes = [IsAdminToken]

    def get(self, _request):
        try:
            return Response({"sessions": tmux.list_sessions()})
        except tmux.TmuxError as err:
            raise TmuxUnavailable(str(err)) from err


class TerminalDetailView(APIView):
    permission_classes = [IsAdminToken]

    def _check(self, name: str) -> None:
        if not tmux.valid_name(name):
            raise ValidationError({"name": "Tên phiên chỉ gồm chữ, số, '-' và '_' (tối đa 32 ký tự)."})

    def patch(self, request, name: str):
        self._check(name)
        new = str(request.data.get("name", "")).strip()
        self._check(new)
        if new == name:
            return Response({"name": new})
        try:
            tmux.rename_session(name, new)
        except tmux.SessionNotFound as err:
            raise NotFound(f"Không có phiên '{name}'.") from err
        except tmux.SessionExists as err:
            raise ValidationError({"name": f"Phiên '{new}' đã tồn tại."}) from err
        except tmux.TmuxError as err:
            raise TmuxUnavailable(str(err)) from err
        return Response({"name": new})

    def delete(self, _request, name: str):
        self._check(name)
        try:
            tmux.kill_session(name)
        except tmux.SessionNotFound:
            pass  # already gone — the end state the caller wanted
        except tmux.TmuxError as err:
            raise TmuxUnavailable(str(err)) from err
        return Response(status=status.HTTP_204_NO_CONTENT)


class SnippetSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=60)
    command = serializers.CharField(max_length=2000, trim_whitespace=False)

    def validate_command(self, value: str) -> str:
        if not value.strip():
            raise ValidationError("Lệnh trống.")
        return value


class SnippetsView(APIView):
    permission_classes = [IsAdminToken]
    KEY = "snippets"
    MAX = 100

    def get(self, _request):
        row = OpsPref.objects.filter(key=self.KEY).first()
        return Response({"items": row.data if row else []})

    def put(self, request):
        items = request.data.get("items")
        if not isinstance(items, list) or len(items) > self.MAX:
            raise ValidationError({"items": f"Cần một danh sách tối đa {self.MAX} snippet."})
        ser = SnippetSerializer(data=items, many=True)
        ser.is_valid(raise_exception=True)
        OpsPref.objects.update_or_create(key=self.KEY, defaults={"data": ser.validated_data})
        return Response({"items": ser.validated_data})
