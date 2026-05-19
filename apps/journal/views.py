from __future__ import annotations

from django.db.models import Q
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

    POST   /journal/vibes              -> upsert (user_id, date) — date defaults to local today.
    GET    /journal/vibes              -> list newest first. Optional filters:
                                          ?q=text&mood=😊&tag=work&date_from=YYYY-MM-DD&date_to=...
    DELETE /journal/vibes/{id}         -> remove a single vibe owned by the user.
    GET    /journal/vibes/today        -> today's entry or null.
    GET    /journal/vibes/stats        -> total, streak, mood histogram.
    GET    /journal/vibes/on-this-day  -> entries from same month-day in prior years/months.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = VibeSerializer
    pagination_class = None  # the frontend reads the full list
    lookup_field = "id"

    def get_queryset(self):
        qs = Vibe.objects.filter(user=self.request.user)
        p = self.request.query_params

        q = (p.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(note__icontains=q))

        mood = (p.get("mood") or "").strip()
        if mood:
            qs = qs.filter(mood_emoji=mood)

        tag = (p.get("tag") or "").strip().lower()
        if tag:
            qs = qs.filter(tags__contains=[tag])

        date_from = (p.get("date_from") or "").strip()
        if date_from:
            qs = qs.filter(date__gte=date_from)

        date_to = (p.get("date_to") or "").strip()
        if date_to:
            qs = qs.filter(date__lte=date_to)

        return qs.order_by("-date")

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
        vibe = Vibe.objects.filter(user=request.user, date=local_today()).first()
        return Response(VibeSerializer(vibe).data if vibe else None)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        return Response(VibeStatsSerializer(compute_stats(request.user.id)).data)

    @action(detail=False, methods=["get"], url_path="on-this-day")
    def on_this_day(self, request):
        today = local_today()
        vibes = (
            Vibe.objects
            .filter(user=request.user, date__month=today.month, date__day=today.day, date__lt=today)
            .order_by("-date")
        )
        return Response(VibeSerializer(vibes, many=True).data)
