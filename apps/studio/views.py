"""Artwork CRUD + share/unshare.

Authenticated routes (`/api/v1/artworks*`):
    GET    /artworks                  list current user's saved works
    POST   /artworks                  create a snapshot/upload/favorite
    GET    /artworks/{id}             single row (owner only)
    DELETE /artworks/{id}             delete a single row
    POST   /artworks/{id}/share       mint a public token
    DELETE /artworks/{id}/share       revoke the public token

Public route (`/api/v1/public/artworks/{token}`):
    GET    open anonymous read-only viewer (no auth required).
"""

from __future__ import annotations

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Artwork, _share_token
from .serializers import ArtworkSerializer, PublicArtworkSerializer


class ArtworkViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = ArtworkSerializer
    pagination_class = None
    lookup_field = "id"

    def get_queryset(self):
        return Artwork.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post", "delete"], url_path="share")
    def share(self, request, id=None):
        artwork = self.get_object()
        if request.method == "POST":
            if not artwork.share_token:
                artwork.share_token = _share_token()
                artwork.save(update_fields=["share_token", "updated_at"])
            return Response({"shareToken": artwork.share_token})
        # DELETE
        artwork.share_token = None
        artwork.save(update_fields=["share_token", "updated_at"])
        return Response({"shareToken": None}, status=status.HTTP_200_OK)


class PublicArtworkView(APIView):
    """GET /api/v1/public/artworks/{token} — read-only snapshot for anonymous
    viewers. Returns the same shape as ArtworkSerializer minus the heavy
    `asset_data_url` blob, plus the owner's username for attribution."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, token: str):
        try:
            artwork = Artwork.objects.select_related("user").get(share_token=token)
        except Artwork.DoesNotExist:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(PublicArtworkSerializer(artwork).data)
