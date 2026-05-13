from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ArtworkViewSet,
    ConversationViewSet,
    LLMViewSet,
    PublicArtworkView,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"artworks", ArtworkViewSet, basename="artwork")
router.register(r"studio/llm", LLMViewSet, basename="studio-llm")
router.register(r"studio/conversations", ConversationViewSet, basename="studio-conversation")

urlpatterns = router.urls + [
    path(
        "public/artworks/<str:token>",
        PublicArtworkView.as_view(),
        name="public-artwork",
    ),
]
