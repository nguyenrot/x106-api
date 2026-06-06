from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import HabitAccountCreateView, HabitLogViewSet, HabitMeView, HabitViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"habits", HabitViewSet, basename="habit")
router.register(r"habit-logs", HabitLogViewSet, basename="habit-log")

# Explicit paths first so /habits/accounts + /habits/me win over the router's
# /habits/{id} detail route.
urlpatterns = [
    path("habits/accounts", HabitAccountCreateView.as_view(), name="habit-create-account"),
    path("habits/me", HabitMeView.as_view(), name="habit-me"),
] + router.urls
