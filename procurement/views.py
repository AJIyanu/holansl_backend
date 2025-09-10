from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import ClientRequest, SupplierQuote, PurchaseOrder, POTracker
from rest_framework.permissions import DjangoModelPermissions
from .serializers import (
    ClientRequestSerializer,
    SupplierQuoteSerializer,
    PurchaseOrderSerializer,
    POTrackerSerializer,
)


class ClientRequestViewSet(viewsets.ModelViewSet):
    queryset = ClientRequest.objects.all().order_by("-created_at")
    serializer_class = ClientRequestSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "client", "item_name", "brand"]
    search_fields = ["item_name", "specification", "model", "brand", "comments"]
    ordering_fields = ["created_at", "updated_at", "quantity"]
    permission_classes = [DjangoModelPermissions]


class SupplierQuoteViewSet(viewsets.ModelViewSet):
    queryset = SupplierQuote.objects.all().order_by("-created_at")
    serializer_class = SupplierQuoteSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["import_type", "supplier", "client_request"]
    search_fields = ["comments", "currency"]
    ordering_fields = ["created_at", "price", "quoted_price", "lead_time_days"]
    permission_classes = [DjangoModelPermissions]


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.all().order_by("-created_at")
    serializer_class = PurchaseOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "client", "supplier_quote"]
    search_fields = ["po_number"]
    ordering_fields = ["created_at", "expiry_date", "price", "quantity"]
    permission_classes = [DjangoModelPermissions]


class POTrackerViewSet(viewsets.ModelViewSet):
    queryset = POTracker.objects.all().order_by("-updated_at")
    serializer_class = POTrackerSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "purchase_order"]
    search_fields = ["description"]
    ordering_fields = ["updated_at"]
    permission_classes = [DjangoModelPermissions]
