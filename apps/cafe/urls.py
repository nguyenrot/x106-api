from django.urls import path

from .views import (
    PublicCafeReviewDetailView,
    PublicCafeReviewListView,
    PublicCafeTagsView,
)

urlpatterns = [
    path("cafe/reviews", PublicCafeReviewListView.as_view(), name="cafe-review-list"),
    path("cafe/tags", PublicCafeTagsView.as_view(), name="cafe-tags"),
    path("cafe/reviews/<slug:slug>", PublicCafeReviewDetailView.as_view(), name="cafe-review-detail"),
]
