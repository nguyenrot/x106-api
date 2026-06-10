from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AdminCafeAgentRunViewSet, AdminCafeImageUploadView, AdminCafeReviewViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"reviews", AdminCafeReviewViewSet, basename="admin-cafe-review")
router.register(r"agent/runs", AdminCafeAgentRunViewSet, basename="admin-cafe-agent-run")

urlpatterns = [
    path("uploads/image", AdminCafeImageUploadView.as_view(), name="admin-cafe-upload-image"),
    *router.urls,
]
