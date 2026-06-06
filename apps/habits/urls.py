from rest_framework.routers import DefaultRouter

from .views import HabitLogViewSet, HabitViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"habits", HabitViewSet, basename="habit")
router.register(r"habit-logs", HabitLogViewSet, basename="habit-log")

urlpatterns = router.urls
