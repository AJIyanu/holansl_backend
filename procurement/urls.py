from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AnalyticsViewSet,
    ClientQuotationViewSet,
    ItemImageViewSet,
    ItemRequestViewSet,
    RequestedItemViewSet,
    SupplierQuoteViewSet,
    PurchaseOrderViewSet,
    PurchaseViewSet,
    WaybillViewSet,
    ItemVerdictViewSet,
)

router = DefaultRouter()
router.register("item-requests", ItemRequestViewSet, basename="item-request")
router.register("items", RequestedItemViewSet, basename="requested-item")
router.register("item-images", ItemImageViewSet, basename="item-image")
router.register("analytics", AnalyticsViewSet, basename="procurement-analytics")
router.register("supplier-quotes", SupplierQuoteViewSet, basename="supplier-quote")
router.register(
    "client-quotations", ClientQuotationViewSet, basename="client-quotation"
)
router.register("purchase-orders", PurchaseOrderViewSet, basename="purchase-order")
router.register("purchases", PurchaseViewSet, basename="purchase")
router.register("waybills", WaybillViewSet, basename="waybill")
router.register("item-verdicts", ItemVerdictViewSet, basename="item-verdict")

urlpatterns = [path("", include(router.urls))]
