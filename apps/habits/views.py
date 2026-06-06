from __future__ import annotations

from django.db.models import Max
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tz import local_today
from apps.ledger.defaults import seed_default_categories
from apps.ledger.models import LedgerAccount

from . import services
from .auth import HabitTokenAuthentication, hash_token
from .models import Habit, HabitLog, HabitType
from .serializers import (
    CreateAccountSerializer,
    HabitLogSerializer,
    HabitSerializer,
    ReorderSerializer,
    UpsertHabitLogSerializer,
)


class HabitAccountCreateView(APIView):
    """POST /habits/accounts — public — create a SHARED account with a chosen token.

    The account is a LedgerAccount, so the same token works on /ledger/* too.
    Body: {"token": "10-char-alnum"}. Raw token is never echoed back; the server
    stores only its SHA-256 hash. 409 if the hash collides. Ledger's default
    categories are seeded so the token is immediately usable on both services."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CreateAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_hash = hash_token(serializer.validated_data["token"])

        if LedgerAccount.objects.filter(token_hash=token_hash).exists():
            return Response(
                {"error": "token_taken", "detail": "Token này đã có người dùng. Chọn token khác."},
                status=status.HTTP_409_CONFLICT,
            )
        account = LedgerAccount.objects.create(token_hash=token_hash)
        seed_default_categories(account)  # keep the shared account usable on ledger too
        return Response(
            {"id": account.id, "created_at": account.created_at},
            status=status.HTTP_201_CREATED,
        )


class HabitMeView(APIView):
    """GET /habits/me — verify the bearer token and return account info."""

    authentication_classes = [HabitTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"id": request.user.id, "created_at": request.user.created_at})


class HabitViewSet(viewsets.ModelViewSet):
    """habits.kynguyen.cc — habit definitions (token-scoped to the account)."""

    authentication_classes = [HabitTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = HabitSerializer
    pagination_class = None
    lookup_field = "id"

    def get_queryset(self):
        qs = Habit.objects.filter(account=self.request.user)
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
        kwargs = {"account": self.request.user}
        if self.request.data.get("sort_order") is None:
            last = Habit.objects.filter(account=self.request.user).aggregate(m=Max("sort_order"))["m"]
            kwargs["sort_order"] = (last or 0) + 1
        serializer.save(**kwargs)

    # ── custom actions ─────────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        today = local_today()
        ws = services.week_start(today)
        habits = Habit.objects.filter(account=request.user, archived=False).order_by(
            "sort_order", "created_at"
        )
        logs_today = {
            log.habit_id: log for log in HabitLog.objects.filter(account=request.user, date=today)
        }

        week_counts: dict[str, int] = {}
        for r in HabitLog.objects.filter(
            account=request.user, completed=True, date__gte=ws, date__lte=today
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
        owned = {h.id: h for h in Habit.objects.filter(account=request.user)}
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
    """Check-ins. One row per (habit, date) — POST upserts."""

    authentication_classes = [HabitTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = HabitLogSerializer
    pagination_class = None
    lookup_field = "id"

    def get_queryset(self):
        qs = HabitLog.objects.filter(account=self.request.user)
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

        habit = Habit.objects.filter(account=request.user, id=data["habit"]).first()
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
            defaults={"account": request.user, "count": count, "completed": completed, "note": note},
        )
        return Response(HabitLogSerializer(log).data, status=status.HTTP_200_OK)
