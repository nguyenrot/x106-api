from __future__ import annotations

from datetime import date as date_cls

from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.tz import local_today

from .models import StreakFreeze, Vibe
from .serializers import (
    ApplyFreezeSerializer,
    UpsertVibeSerializer,
    VibeSerializer,
    VibeStatsSerializer,
)
from .services import compute_stats


FREEZES_PER_MONTH = 1


def _used_in_month(user_id: str, ref: date_cls) -> int:
    """Freezes CONSUMED during `ref`'s calendar month (by `used_at`, i.e. when
    the user spent the freeze) — not by the day it was applied to. The quota
    is "one freeze action per month", so freezing a day from last month still
    burns this month's allowance."""
    return StreakFreeze.objects.filter(
        user_id=user_id,
        used_at__year=ref.year,
        used_at__month=ref.month,
    ).count()


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
    pagination_class = None
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
        # If the user backfills an entry on a previously-frozen day, the freeze
        # is no longer needed — release it so the freeze count rolls back up.
        StreakFreeze.objects.filter(user=request.user, applied_date=date).delete()
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


class FreezeViewSet(viewsets.ViewSet):
    """journal.kynguyen.cc — streak freezes.

    GET  /journal/freezes        -> {available_this_month, total_used, used_dates}
    POST /journal/freezes/apply  -> {date} apply a freeze to a past missed day.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        today = local_today()
        used_this_month = _used_in_month(request.user.id, today)
        used_dates = list(
            StreakFreeze.objects
            .filter(user=request.user)
            .order_by("-applied_date")
            .values_list("applied_date", flat=True)
        )
        return Response({
            "available_this_month": max(FREEZES_PER_MONTH - used_this_month, 0),
            "freezes_per_month": FREEZES_PER_MONTH,
            "total_used": len(used_dates),
            "used_dates": [d.isoformat() for d in used_dates],
        })

    @action(detail=False, methods=["post"], url_path="apply")
    def apply(self, request):
        serializer = ApplyFreezeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        applied = serializer.validated_data["date"]
        today = local_today()

        if applied >= today:
            raise ValidationError({"date": "Freezes can only be applied to past days."})

        if Vibe.objects.filter(user=request.user, date=applied).exists():
            raise ValidationError({"date": "That day already has an entry — no freeze needed."})

        if StreakFreeze.objects.filter(user=request.user, applied_date=applied).exists():
            raise ValidationError({"date": "That day is already frozen."})

        if _used_in_month(request.user.id, today) >= FREEZES_PER_MONTH:
            raise ValidationError({"date": "No freezes left this month."})

        StreakFreeze.objects.create(user=request.user, applied_date=applied)
        return Response({"message": "freeze applied", "date": applied.isoformat()}, status=status.HTTP_201_CREATED)
