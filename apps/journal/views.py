from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.tz import local_today

from .models import Vibe
from .serializers import (
    UpsertVibeSerializer,
    VibeSerializer,
    VibeStatsSerializer,
)
from .services import compute_stats


class VibeViewSet(
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """journal.kynguyen.cc — vibes (mood entries).

    POST   /journal/vibes        -> upsert (user_id, date) — date defaults to local today.
    GET    /journal/vibes        -> list newest first.
    DELETE /journal/vibes/{id}   -> remove a single vibe owned by the user.
    GET    /journal/vibes/today  -> today's entry or null.
    GET    /journal/vibes/stats  -> total, streak, mood histogram.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = VibeSerializer
    pagination_class = None  # the frontend reads the full list
    lookup_field = "id"

    def get_queryset(self):
        return Vibe.objects.filter(user=self.request.user).order_by("-date")

    def create(self, request, *args, **kwargs):
        serializer = UpsertVibeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        date = serializer.validated_data.get("date") or local_today()
        Vibe.objects.update_or_create(
            user=request.user,
            date=date,
            defaults={
                "mood_emoji": serializer.validated_data["mood_emoji"],
                "title": serializer.validated_data["title"],
                "note": serializer.validated_data.get("note") or None,
                "tags": serializer.validated_data.get("tags") or [],
            },
        )
        return Response({"message": "vibe saved"})

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        vibe = self.get_queryset().filter(date=local_today()).first()
        return Response(VibeSerializer(vibe).data if vibe else None)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        return Response(VibeStatsSerializer(compute_stats(request.user.id)).data)
