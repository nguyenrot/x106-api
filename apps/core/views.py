from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .services.weather import get_weather


@api_view(["GET"])
@permission_classes([AllowAny])
def health(_request):
    return Response({"status": "ok", "service": "x106-api"})


@api_view(["GET"])
@permission_classes([AllowAny])
def now_weather(_request):
    """Live weather for Đà Nẵng (Mỹ Khê). Cached 15 min in-process."""
    return Response(get_weather())
