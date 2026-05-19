from rest_framework.routers import DefaultRouter

from .views import FreezeViewSet, VibeViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"vibes", VibeViewSet, basename="vibe")
router.register(r"freezes", FreezeViewSet, basename="freeze")

urlpatterns = router.urls
