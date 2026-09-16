"""Supported procurement endpoints; legacy URLs remain removed."""

from rest_framework.routers import DefaultRouter
from . import views as v

router = DefaultRouter()
for path, view in [
    ("lookups", v.LookupViewSet),
    ("document-register", v.DocumentRegisterViewSet),
    ("document-versions", v.DocumentVersionViewSet),
    ("requests", v.RequestViewSet),
    ("items", v.ItemViewSet),
    ("supplier-quotations", v.OfferViewSet),
    ("quotations", v.QuotationViewSet),
    ("quotation-revisions", v.RevisionViewSet),
    ("purchase-orders", v.PurchaseOrderViewSet),
    ("supplier-purchases", v.PurchaseViewSet),
    ("goods-receipts", v.ReceiptViewSet),
    ("deliveries", v.DeliveryViewSet),
    ("delivery-outcomes", v.OutcomeViewSet),
    ("outcome-lines", v.OutcomeLineViewSet),
    ("rejection-actions", v.RejectionViewSet),
    ("attempts", v.AttemptViewSet),
    ("events", v.EventViewSet),
    ("documents", v.DocumentViewSet),
    ("costs", v.CostViewSet),
    ("finance-requests", v.FinanceViewSet),
    ("task-links", v.TaskLinkViewSet),
    ("capabilities", v.CapabilityViewSet),
]:
    router.register(path, view, basename="procurement-" + path)
urlpatterns = router.urls
