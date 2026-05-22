from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    MessagePollView,
    MessageRetryView,
    SessionViewSet,
    SettingsView,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"sessions", SessionViewSet, basename="console-session")

urlpatterns = [
    path("messages/<str:message_id>", MessagePollView.as_view(), name="console-message"),
    path("messages/<str:message_id>/retry", MessageRetryView.as_view(), name="console-message-retry"),
    path("settings", SettingsView.as_view(), name="console-settings"),
] + router.urls
