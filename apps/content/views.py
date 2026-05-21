from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminToken

from .models import SiteContent
from .serializers import SiteContentSerializer, UpsertSectionSerializer


class PublicContentView(APIView):
    """GET /api/v1/content/{app}/{section}"""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, _request, app: str, section: str):
        row = SiteContent.objects.filter(app=app, section=section).first()
        if row is None:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(SiteContentSerializer(row).data)


class AdminContentListView(APIView):
    """GET /api/v1/admin/content/{app}"""

    permission_classes = [IsAdminToken]

    def get(self, _request, app: str):
        rows = SiteContent.objects.filter(app=app).order_by("section")
        return Response(SiteContentSerializer(rows, many=True).data)


class AdminContentSectionView(APIView):
    """PUT/DELETE /api/v1/admin/content/{app}/{section}"""

    permission_classes = [IsAdminToken]

    def put(self, request, app: str, section: str):
        serializer = UpsertSectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _row, created = SiteContent.objects.update_or_create(
            app=app,
            section=section,
            defaults={"data": serializer.validated_data["data"]},
        )
        return Response({"message": "created" if created else "updated"})

    def delete(self, _request, app: str, section: str):
        deleted, _ = SiteContent.objects.filter(app=app, section=section).delete()
        if deleted == 0:
            return Response({"error": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"message": "deleted"})
