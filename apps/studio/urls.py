from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ArtworkViewSet, LLMViewSet, PublicArtworkView

router = DefaultRouter(trailing_slash=False)
router.register(r"artworks", ArtworkViewSet, basename="artwork")
router.register(r"studio/llm", LLMViewSet, basename="studio-llm")

urlpatterns = router.urls + [
    path(
        "public/artworks/<str:token>",
        PublicArtworkView.as_view(),
        name="public-artwork",
    ),
]
