from rest_framework.routers import DefaultRouter

from .views import AdminQuoteViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"", AdminQuoteViewSet, basename="admin-quote")

urlpatterns = router.urls
