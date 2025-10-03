from rest_framework.routers import DefaultRouter
from .views import (
    ClientRequestViewSet,
    SupplierQuoteViewSet,
    PurchaseOrderViewSet,
    POTrackerViewSet,
)

router = DefaultRouter()
router.register(
    r"client-requests",
    ClientRequestViewSet,
    basename="clientrequest")
router.register(
    r"supplier-quotes",
    SupplierQuoteViewSet,
    basename="supplierquote")
router.register(
    r"purchase-orders",
    PurchaseOrderViewSet,
    basename="purchaseorder")
router.register(r"po-tracker", POTrackerViewSet, basename="potracker")

urlpatterns = router.urls
