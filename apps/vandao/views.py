"""GET/PUT /api/v1/vandao/save — the player's own cloud save."""

from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GameSave
from .serializers import GameSaveSerializer, PutSaveSerializer


class GameSaveView(APIView):
    """The save is always the caller's own — there is no id in the URL to tamper with."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        row = GameSave.objects.filter(user=request.user).first()
        return Response({"save": GameSaveSerializer(row).data if row else None})

    def put(self, request):
        serializer = PutSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        with transaction.atomic():
            row = GameSave.objects.select_for_update().filter(user=request.user).first()
            current = row.revision if row else 0
            if not payload["force"] and payload["baseRevision"] != current:
                # Another device wrote since this one last synced. Hand back the server
                # copy so the client can show both and let the player pick.
                return Response(
                    {
                        "error": "conflict",
                        "save": GameSaveSerializer(row).data if row else None,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            row, _created = GameSave.objects.update_or_create(
                user=request.user,
                defaults={"data": payload["data"], "revision": current + 1},
            )

        return Response(GameSaveSerializer(row).data)
