from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    LedgerAccountCreateView,
    LedgerCategoryViewSet,
    LedgerMeView,
    LedgerTransactionViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"transactions", LedgerTransactionViewSet, basename="ledger-transaction")
router.register(r"categories", LedgerCategoryViewSet, basename="ledger-category")

urlpatterns = [
    path("accounts", LedgerAccountCreateView.as_view(), name="ledger-create-account"),
    path("me", LedgerMeView.as_view(), name="ledger-me"),
] + router.urls
