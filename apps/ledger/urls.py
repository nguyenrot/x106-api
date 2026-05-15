from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    LedgerAccountCreateView,
    LedgerCategoriesView,
    LedgerMeView,
    LedgerTransactionViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"transactions", LedgerTransactionViewSet, basename="ledger-transaction")

urlpatterns = [
    path("accounts", LedgerAccountCreateView.as_view(), name="ledger-create-account"),
    path("me", LedgerMeView.as_view(), name="ledger-me"),
    path("categories", LedgerCategoriesView.as_view(), name="ledger-categories"),
] + router.urls
