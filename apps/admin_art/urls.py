from rest_framework.routers import DefaultRouter

from .views import AdminArtViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"art", AdminArtViewSet, basename="admin-art")

urlpatterns = router.urls
