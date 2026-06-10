"""Ledger endpoints — accounts, categories (CRUD), transactions, summary."""

from __future__ import annotations

from django.db import IntegrityError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tz import local_today

from .auth import LedgerTokenAuthentication, hash_token
from .defaults import seed_default_categories
from .models import LedgerAccount, LedgerCategoryRow, LedgerTransaction
from .serializers import (
    CategoryCreateSerializer,
    CategoryReorderSerializer,
    CategoryUpdateSerializer,
    CreateAccountSerializer,
    LedgerAccountSerializer,
    LedgerCategorySerializer,
    LedgerTransactionSerializer,
    _make_unique_slug,
)
from .services import compute_summary, totals_for


class LedgerAccountCreateView(APIView):
    """POST /ledger/accounts — public — create account with a user-chosen token.

    Body: {"token": "10-char-alnum"}. The raw token is never echoed back; the
    server stores only its SHA-256 hash. 409 if the hash collides with an
    existing account — pick a different token. Default categories are seeded
    on creation so the new account is immediately usable.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CreateAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["token"]
        token_hash = hash_token(raw)

        if LedgerAccount.objects.filter(token_hash=token_hash).exists():
            return Response(
                {"error": "token_taken", "detail": "Token này đã có người dùng. Hãy chọn token khác."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            account = LedgerAccount.objects.create(token_hash=token_hash)
        except IntegrityError:
            # Race: another request claimed the same token between the
            # exists() check and the insert. Same 409 as above.
            return Response(
                {"error": "token_taken", "detail": "Token này đã có người dùng. Hãy chọn token khác."},
                status=status.HTTP_409_CONFLICT,
            )
        seed_default_categories(account)
        return Response(
            {"id": account.id, "created_at": account.created_at},
            status=status.HTTP_201_CREATED,
        )


class LedgerMeView(APIView):
    """GET /ledger/me — verify the bearer token and return account info."""

    authentication_classes = [LedgerTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(LedgerAccountSerializer(request.user).data)


# ── Categories ────────────────────────────────────────────────────────────


class LedgerCategoryViewSet(viewsets.ViewSet):
    """User-editable categories. Income and expense lists are independent."""

    authentication_classes = [LedgerTokenAuthentication]
    permission_classes = [IsAuthenticated]
    lookup_value_regex = r"[^/]+"

    def _account_qs(self, request, include_archived: bool = False):
        qs = LedgerCategoryRow.objects.filter(account=request.user)
        if not include_archived:
            qs = qs.filter(is_archived=False)
        return qs

    def list(self, request):
        rows = list(self._account_qs(request).order_by("kind", "position", "created_at"))
        income = [LedgerCategorySerializer(r).data for r in rows if r.kind == "income"]
        expense = [LedgerCategorySerializer(r).data for r in rows if r.kind == "expense"]
        return Response({"income": income, "expense": expense})

    def create(self, request):
        serializer = CategoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        name = serializer.validated_data["name"]
        color = serializer.validated_data.get("color", "#94a3b8")
        position = serializer.validated_data.get("position")
        if position is None:
            # Append to the end of the kind's list.
            last = (
                self._account_qs(request)
                .filter(kind=kind)
                .order_by("-position")
                .first()
            )
            position = (last.position + 1) if last else 0

        row = LedgerCategoryRow.objects.create(
            account=request.user,
            kind=kind,
            slug=_make_unique_slug(request.user, kind, name),
            name=name,
            color=color,
            position=position,
        )
        return Response(LedgerCategorySerializer(row).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        try:
            row = self._account_qs(request, include_archived=True).get(id=pk)
        except LedgerCategoryRow.DoesNotExist:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CategoryUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field in ("name", "color", "position"):
            if field in serializer.validated_data:
                setattr(row, field, serializer.validated_data[field])
        row.save()
        return Response(LedgerCategorySerializer(row).data)

    def destroy(self, request, pk=None):
        """Soft-delete (archive). Transactions that referenced this category
        still resolve to it for display purposes."""
        try:
            row = self._account_qs(request, include_archived=True).get(id=pk)
        except LedgerCategoryRow.DoesNotExist:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        if row.is_archived:
            return Response(status=status.HTTP_204_NO_CONTENT)
        row.is_archived = True
        row.save(update_fields=["is_archived", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="restore")
    def restore(self, request, pk=None):
        try:
            row = self._account_qs(request, include_archived=True).get(id=pk)
        except LedgerCategoryRow.DoesNotExist:
            return Response({"error": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        row.is_archived = False
        row.save(update_fields=["is_archived", "updated_at"])
        return Response(LedgerCategorySerializer(row).data)

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        serializer = CategoryReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]
        order = serializer.validated_data["order"]

        rows = {
            r.id: r
            for r in self._account_qs(request).filter(kind=kind, id__in=order)
        }
        updated = []
        for index, row_id in enumerate(order):
            row = rows.get(row_id)
            if row is None:
                continue
            row.position = index
            row.save(update_fields=["position", "updated_at"])
            updated.append(LedgerCategorySerializer(row).data)
        return Response({"items": updated})


# ── Transactions ──────────────────────────────────────────────────────────


class LedgerTransactionViewSet(viewsets.ModelViewSet):
    """CRUD on transactions + /today + /summary."""

    authentication_classes = [LedgerTokenAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = LedgerTransactionSerializer
    pagination_class = None
    lookup_value_regex = r"[^/]+"

    def get_queryset(self):
        qs = LedgerTransaction.objects.filter(account=self.request.user)
        params = self.request.query_params
        if params.get("date"):
            qs = qs.filter(occurred_on=params["date"])
        if params.get("from"):
            qs = qs.filter(occurred_on__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(occurred_on__lte=params["to"])
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        if params.get("category"):
            qs = qs.filter(category=params["category"])
        return qs.order_by("-occurred_on", "-created_at")

    def perform_create(self, serializer):
        if "occurred_on" not in serializer.validated_data:
            serializer.validated_data["occurred_on"] = local_today()
        serializer.save(account=self.request.user)

    @action(detail=False, methods=["get"], url_path="today")
    def today(self, request):
        today = local_today()
        rows = list(
            LedgerTransaction.objects.filter(account=request.user, occurred_on=today).order_by(
                "-created_at"
            )
        )
        return Response(
            {
                "date": today.strftime("%Y-%m-%d"),
                "transactions": LedgerTransactionSerializer(rows, many=True).data,
                **totals_for(rows),
            }
        )

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        params = request.query_params
        today_str = local_today().strftime("%Y-%m-%d")
        date_from = params.get("from") or today_str
        date_to = params.get("to") or today_str
        group_by = (params.get("group_by") or "day").lower()
        if group_by not in {"day", "month"}:
            group_by = "day"
        return Response(compute_summary(request.user, date_from, date_to, group_by))
