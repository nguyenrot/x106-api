from __future__ import annotations

from django.db.models import Max
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.tz import local_today

from . import services
from .models import Habit, HabitLog, HabitType
from .serializers import (
    HabitLogSerializer,
    HabitSerializer,
    ReorderSerializer,
    UpsertHabitLogSerializer,
)


class HabitViewSet(viewsets.ModelViewSet):
    """habits.kynguyen.cc — habit definitions.

    GET    /habits                 -> list (?include_archived, ?category, ?tag)
    POST   /habits                 -> create
    GET    /habits/{id}            -> retrieve
    PATCH  /habits/{id}            -> update
    DELETE /habits/{id}            -> delete (and its logs cascade)
    GET    /habits/today           -> habits due today + today's log/progress
    GET    /habits/stats           -> streaks, rates, heatmap
    POST   /habits/reorder         -> {order:[{id,sort_order}]}
    POST   /habits/{id}/archive    -> archive
    POST   /habits/{id}/unarchive  -> unarchive
    """

    permission_classes = [IsAuthenticated]
    serializer_class = HabitSerializer
    pagination_class = None
    lookup_field = "id"

    def get_queryset(self):
        qs = Habit.objects.filter(user=self.request.user)
        p = self.request.query_params
        if (p.get("include_archived") or "").lower() not in ("1", "true", "yes"):
            qs = qs.filter(archived=False)
        category = (p.get("category") or "").strip()
        if category:
            qs = qs.filter(category=category)
        tag = (p.get("tag") or "").strip().lower()
        if tag:
            qs = qs.filter(tags__contains=[tag])
        return qs.order_by("sort_order", "created_at")

    def perform_create(self, serializer):
        kwargs = {"user": self.request.user}
        if self.request.data.get("sort_order") is None:
            last = Habit.objects.filter(user=self.request.user).aggregate(m=Max("sort_order"))["m"]
            kwargs["sort_order"] = (last or 0) + 1
        serializer.save(**kwargs)

    # ── custom actions ─────────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        today = local_today()
        ws = services.week_start(today)
        habits = Habit.objects.filter(user=request.user, archived=False).order_by("sort_order", "created_at")
        logs_today = {log.habit_id: log for log in HabitLog.objects.filter(user=request.user, date=today)}

        # per-week completed counts (for weekly_count progress)
        week_counts: dict[str, int] = {}
        for r in HabitLog.objects.filter(
            user=request.user, completed=True, date__gte=ws, date__lte=today
        ).values("habit_id"):
            week_counts[r["habit_id"]] = week_counts.get(r["habit_id"], 0) + 1

        items = []
        for h in habits:
            if not services.is_due_today(h, today):
                continue
            log = logs_today.get(h.id)
            week = None
            if h.frequency == "weekly_count":
                week = {"count": week_counts.get(h.id, 0), "target": h.weekly_target or 1}
            items.append({
                "habit": HabitSerializer(h).data,
                "log": HabitLogSerializer(log).data if log else None,
                "done": bool(log and log.completed),
                "week": week,
            })
        return Response({"date": today.isoformat(), "items": items})

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        return Response(services.compute_stats(request.user))

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        owned = {h.id: h for h in Habit.objects.filter(user=request.user)}
        updated = []
        for row in serializer.validated_data["order"]:
            hid = str(row.get("id", ""))
            if hid in owned and "sort_order" in row:
                h = owned[hid]
                h.sort_order = int(row["sort_order"])
                updated.append(h)
        if updated:
            Habit.objects.bulk_update(updated, ["sort_order"])
        return Response({"updated": len(updated)})

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, id=None):
        habit = self.get_object()
        habit.archived = True
        habit.archived_at = timezone.now()
        habit.save(update_fields=["archived", "archived_at", "updated_at"])
        return Response(HabitSerializer(habit).data)

    @action(detail=True, methods=["post"], url_path="unarchive")
    def unarchive(self, request, id=None):
        habit = self.get_object()
        habit.archived = False
        habit.archived_at = None
        habit.save(update_fields=["archived", "archived_at", "updated_at"])
        return Response(HabitSerializer(habit).data)


class HabitLogViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Check-ins. One row per (habit, date) — POST upserts.

    GET    /habit-logs?habit=&date_from=&date_to=  -> list (calendar/heatmap data)
    POST   /habit-logs                             -> upsert {habit, date?, count?, note?}
    DELETE /habit-logs/{id}                        -> remove (uncheck)
    """

    permission_classes = [IsAuthenticated]
    serializer_class = HabitLogSerializer
    pagination_class = None
    lookup_field = "id"

    def get_queryset(self):
        qs = HabitLog.objects.filter(user=self.request.user)
        p = self.request.query_params
        habit = (p.get("habit") or "").strip()
        if habit:
            qs = qs.filter(habit_id=habit)
        date_from = (p.get("date_from") or "").strip()
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = (p.get("date_to") or "").strip()
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs.order_by("-date")

    def create(self, request, *args, **kwargs):
        serializer = UpsertHabitLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        habit = Habit.objects.filter(user=request.user, id=data["habit"]).first()
        if habit is None:
            raise NotFound("Habit not found.")

        log_date = data.get("date") or local_today()

        if habit.type == HabitType.COUNT:
            target = habit.target_count or 1
            count = data.get("count")
            if count is None:
                count = target  # a bare "check" on a quantitative habit fills the target
            completed = count >= target
        else:
            count = 1 if data.get("count") is None else data["count"]
            completed = count >= 1

        note = (data.get("note") or "").strip() or None

        log, _created = HabitLog.objects.update_or_create(
            habit=habit,
            date=log_date,
            defaults={"user": request.user, "count": count, "completed": completed, "note": note},
        )
        return Response(HabitLogSerializer(log).data, status=status.HTTP_200_OK)
