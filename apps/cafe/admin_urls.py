from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AdminCafeImageUploadView, AdminCafeReviewViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"reviews", AdminCafeReviewViewSet, basename="admin-cafe-review")

urlpatterns = [
    path("uploads/image", AdminCafeImageUploadView.as_view(), name="admin-cafe-upload-image"),
    *router.urls,
]
