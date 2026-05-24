from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BaogiaViewSet, PublicBaogiaView, QuoteViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"baogia", BaogiaViewSet, basename="baogia")
router.register(r"", QuoteViewSet, basename="quote")

urlpatterns = [
    path("baogia/public/<str:share_token>", PublicBaogiaView.as_view(), name="baogia-public"),
    *router.urls,
]
