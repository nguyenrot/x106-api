"""Ledger endpoints — accounts, categories, transactions, summary."""

from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tz import local_today

from .auth import LedgerTokenAuthentication, hash_token
from .models import LedgerAccount, LedgerCategory, LedgerTransaction
from .serializers import (
    CreateAccountSerializer,
    LedgerAccountSerializer,
    LedgerTransactionSerializer,
)
from .services import compute_summary, totals_for


class LedgerAccountCreateView(APIView):
    """POST /ledger/accounts — public — create account with a user-chosen token.

    Body: {"token": "10-char-alnum"}. The raw token is never echoed back; the
    server stores only its SHA-256 hash. 409 if the hash collides with an
    existing account — pick a different token.
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
        account = LedgerAccount.objects.create(token_hash=token_hash)
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


class LedgerCategoriesView(APIView):
    """GET /ledger/categories — public — predefined Vietnamese-labeled categories."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, _request):
        return Response(
            [{"id": value, "label": label} for value, label in LedgerCategory.choices]
        )


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
