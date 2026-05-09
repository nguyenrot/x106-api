from rest_framework.routers import DefaultRouter

from .views import VibeViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"vibes", VibeViewSet, basename="vibe")

urlpatterns = router.urls
