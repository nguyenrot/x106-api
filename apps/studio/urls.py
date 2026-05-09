from rest_framework.routers import DefaultRouter

from .views import ArtworkViewSet, LLMViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"artworks", ArtworkViewSet, basename="artwork")
router.register(r"studio/llm", LLMViewSet, basename="studio-llm")

urlpatterns = router.urls
