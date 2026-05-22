from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ExecViewSet,
    LogsView,
    MessagePollView,
    SessionViewSet,
    SettingsView,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"sessions", SessionViewSet, basename="console-session")
router.register(r"execs", ExecViewSet, basename="console-exec")

urlpatterns = [
    path("messages/<str:message_id>", MessagePollView.as_view(), name="console-message"),
    path("logs", LogsView.as_view(), name="console-logs"),
    path("settings", SettingsView.as_view(), name="console-settings"),
] + router.urls
