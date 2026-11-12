from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AnalyticsViewSet,
    ItemImageViewSet,
    ItemRequestViewSet,
    RequestedItemViewSet,
)

router = DefaultRouter()
router.register("item-requests", ItemRequestViewSet, basename="item-request")
router.register("items", RequestedItemViewSet, basename="requested-item")
router.register("item-images", ItemImageViewSet, basename="item-image")
router.register("analytics", AnalyticsViewSet, basename="procurement-analytics")

urlpatterns = [path("", include(router.urls))]
