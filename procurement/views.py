import json
import logging

from django.db.models import Count, OuterRef, Prefetch, Subquery
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from crm.models import Party
from holansladminservices.pagination import StandardPagination
from . import analytics, models as m, serializers as z, services
from .filters import ItemRequestFilter, RequestedItemFilter
from .permissions import ProcurementPermission

logger = logging.getLogger(__name__)


def item_queryset():
    latest_price = m.ClientQuotationItem.objects.filter(
        item_id=OuterRef("pk")
    ).order_by("-quotation__quotation_date", "-created_at")
    return (
        m.RequestedItem.objects.select_related("request", "created_by", "completed_by")
        .prefetch_related(
            "images",
            Prefetch(
                "activities",
                queryset=m.ItemActivity.objects.order_by("-occurred_at", "-created_at"),
            ),
        )
        .annotate(
            activity_count=Count("activities", distinct=True),
            latest_quotation_price=Subquery(latest_price.values("unit_price")[:1]),
        )
    )


class ItemRequestViewSet(viewsets.ModelViewSet):
    queryset = m.ItemRequest.objects.select_related(
        "client", "created_by"
    ).prefetch_related(Prefetch("items", queryset=item_queryset()))
    serializer_class = z.ItemRequestSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = ItemRequestFilter
    ordering_fields = ["received_at", "client_name", "created_at"]
    ordering = ["-received_at", "-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request):
        form = z.ItemRequestInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.create_request(request.user, dict(form.validated_data))
        row = self.get_queryset().get(pk=row.pk)
        return Response(self.get_serializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="client-options")
    def client_options(self, request):
        search = request.query_params.get("search", "")[:100]
        rows = (
            Party.objects.active()
            .with_role("CLIENT")
            .filter(display_name__icontains=search)[:30]
        )
        return Response(
            [{"id": str(row.pk), "label": row.display_name} for row in rows]
        )


class RequestedItemViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = item_queryset()
    serializer_class = z.RequestedItemSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = RequestedItemFilter
    ordering_fields = [
        "created_at",
        "name",
        "request__client_name",
        "deadline",
        "urgency",
        "status",
        "updated_at",
    ]
    ordering = ["-created_at"]
    action_permissions = {
        "tracker": "procurement.view_itemtracker",
        "activities": {
            "GET": "procurement.view_itemtracker",
            "POST": "procurement.add_itemactivity",
        },
        "change_status": "procurement.change_itemstatus",
        "edit": "procurement.change_requesteditem",
        "images": {
            "POST": "procurement.add_requesteditemimage",
            "DELETE": "procurement.delete_requesteditemimage",
        },
    }

    @action(detail=True, methods=["get"])
    def tracker(self, request, pk=None):
        item = self.get_object()
        data = self.get_serializer(item).data
        activities = item.activities.order_by("occurred_at", "created_at", "id")
        data["activities"] = z.ActivitySerializer(activities, many=True).data
        data["suggested_titles"] = [
            {"value": code, "label": label}
            for code, label in services.ACTIVITY_TITLES.items()
        ]
        return Response(data)

    @action(detail=True, methods=["get", "post"])
    def activities(self, request, pk=None):
        item = self.get_object()
        if request.method == "GET":
            page = self.paginate_queryset(
                item.activities.order_by("occurred_at", "created_at", "id")
            )
            return self.get_paginated_response(
                z.ActivitySerializer(page, many=True).data
            )
        form = z.ActivityInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.add_activity(request.user, item, form.validated_data)
        return Response(z.ActivitySerializer(row).data, status=201)

    @action(detail=True, methods=["patch"])
    def edit(self, request, pk=None):
        form = z.ItemEditInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.edit_item(request.user, self.get_object(), form.validated_data)
        return Response(self.get_serializer(item_queryset().get(pk=row.pk)).data)

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        form = z.StatusInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.change_status(
            request.user, self.get_object(), form.validated_data
        )
        return Response(self.get_serializer(item_queryset().get(pk=row.pk)).data)

    @action(
        detail=True,
        methods=["post", "delete"],
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def images(self, request, pk=None):
        item = self.get_object()
        if request.method == "DELETE":
            form = z.ImageDeleteInput(data=request.data)
            form.is_valid(raise_exception=True)
            removed = services.remove_images(
                request.user, item, form.validated_data["images"]
            )
            return Response({"removed": len(removed)})
        files = request.FILES.getlist("files")
        if not files and request.FILES.get("file"):
            files = [request.FILES["file"]]
        form = z.ImagesInput(
            data={"files": files, "caption": request.data.get("caption", "")}
        )
        form.is_valid(raise_exception=True)
        rows = services.upload_images(
            request.user,
            item,
            form.validated_data["files"],
            form.validated_data.get("caption", ""),
        )
        return Response(z.ItemImageSerializer(rows, many=True).data, status=201)


class ItemImageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = m.RequestedItemImage.objects.select_related("item__request")
    serializer_class = z.ItemImageSerializer
    permission_classes = [ProcurementPermission]
    action_permissions = {"download": "procurement.download_itemimage"}
    http_method_names = ["get", "head", "options"]

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        row = self.get_object()
        downloaded = services.download_image(row)
        response = FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=True,
            filename=downloaded.filename,
        )
        response["Cache-Control"] = "private, no-store"
        return response


class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [ProcurementPermission]
    action_permissions = {
        "summary": "procurement.view_itemtracker",
        "ai_insight": "procurement.view_itemtracker",
    }

    @action(detail=False, methods=["get"])
    def summary(self, request):
        try:
            result = analytics.get_cached_analytics(
                request.query_params.get("range", "year")
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_analytics_range"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)

    @action(detail=False, methods=["get"], url_path="ai-insight")
    def ai_insight(self, request):
        try:
            result = analytics.get_cached_ai_analytics(
                request.query_params.get("range", "year")
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_analytics_range"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except analytics.AIConfigurationError:
            return Response(
                {
                    "detail": "AI Procurement analysis is temporarily unavailable.",
                    "code": "ai_insight_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Procurement AI analytics generation failed")
            return Response(
                {
                    "detail": "AI Procurement analysis could not be generated.",
                    "code": "ai_insight_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)


class SupplierQuoteViewSet(viewsets.ModelViewSet):
    queryset = m.SupplierQuote.objects.select_related(
        "supplier", "created_by"
    ).prefetch_related("items__item")
    serializer_class = z.SupplierQuoteSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["supplier_name", "supplier_reference", "items__item_name"]
    ordering_fields = ["quotation_date", "expires_on", "created_at", "supplier_name"]
    ordering = ["-quotation_date", "-created_at"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head", "options"]
    action_permissions = {"attachment": "procurement.download_supplierquote"}

    def create(self, request):
        # Use a plain mapping: DRF treats QueryDict nested lists as HTML fields.
        payload = {key: request.data.get(key) for key in request.data}
        if payload.get("expires_on") == "":
            payload.pop("expires_on")
        if isinstance(payload.get("items"), str):
            try:
                payload["items"] = json.loads(payload["items"])
            except json.JSONDecodeError:
                return Response({"items": ["Submit valid item details."]}, status=400)
        form = z.SupplierQuoteInput(data=payload)
        form.is_valid(raise_exception=True)
        quote = services.create_supplier_quote(
            request.user, dict(form.validated_data), request.FILES.get("attachment")
        )
        quote = self.get_queryset().get(pk=quote.pk)
        return Response(self.get_serializer(quote).data, status=201)

    @action(detail=False, methods=["get"], url_path="supplier-options")
    def supplier_options(self, request):
        search = request.query_params.get("search", "")[:100]
        rows = (
            Party.objects.active()
            .with_role("SUPPLIER")
            .filter(display_name__icontains=search)[:30]
        )
        return Response(
            [{"id": str(row.pk), "label": row.display_name} for row in rows]
        )

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        row = self.get_object()
        downloaded = services.download_supplier_attachment(row)
        response = FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=True,
            filename=downloaded.filename,
        )
        response["Cache-Control"] = "private, no-store"
        return response


class ClientQuotationViewSet(viewsets.ModelViewSet):
    queryset = m.ClientQuotation.objects.select_related(
        "client", "created_by", "issued_by"
    ).prefetch_related("items__item", "communications", "revisions")
    serializer_class = z.ClientQuotationSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["reference", "client_name", "items__item_name"]
    ordering_fields = [
        "quotation_date",
        "expires_on",
        "created_at",
        "client_name",
        "status",
    ]
    ordering = ["-quotation_date", "-created_at"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_field = "reference"
    lookup_value_regex = "[^/]+"
    action_permissions = {
        "issue": "procurement.issue_clientquotation",
        "download": "procurement.communicate_clientquotation",
        "print_document": "procurement.communicate_clientquotation",
        "email": "procurement.communicate_clientquotation",
        "partial_update": "procurement.change_clientquotation",
        "remove_document": "procurement.remove_clientquotation",
    }

    def create(self, request):
        form = z.ClientQuotationInput(data=request.data)
        form.is_valid(raise_exception=True)
        quote = services.create_client_quotation(
            request.user, dict(form.validated_data)
        )
        quote = self.get_queryset().get(pk=quote.pk)
        return Response(self.get_serializer(quote).data, status=201)

    def partial_update(self, request, reference=None):
        form = z.ClientQuotationInput(data=request.data)
        form.is_valid(raise_exception=True)
        quote = self.get_object()
        if quote.status == "ISSUED" and not request.user.has_perm(
            "procurement.revise_clientquotation"
        ):
            self.permission_denied(request)
        quote = services.edit_client_quotation(
            request.user, quote, dict(form.validated_data)
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=quote.pk)).data)

    @action(detail=True, methods=["post"])
    def issue(self, request, reference=None):
        quote = services.issue_client_quotation(request.user, self.get_object())
        return Response(self.get_serializer(self.get_queryset().get(pk=quote.pk)).data)

    def _document_response(self, request, method, *, attachment):
        quote = self.get_object()
        downloaded = services.download_client_quotation(quote)
        services.record_quotation_communication(
            request.user, quote, {"method": method, "destination": "", "note": ""}
        )
        response = FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=attachment,
            filename=downloaded.filename,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    @action(detail=True, methods=["get"])
    def download(self, request, reference=None):
        return self._document_response(request, "DOWNLOAD", attachment=True)

    @action(detail=True, methods=["get"], url_path="print")
    def print_document(self, request, reference=None):
        return self._document_response(request, "PRINT", attachment=False)

    @action(detail=True, methods=["post"])
    def email(self, request, reference=None):
        form = z.QuotationEmailInput(data=request.data)
        form.is_valid(raise_exception=True)
        services.email_client_quotation(
            request.user, self.get_object(), form.validated_data
        )
        return Response({"detail": "Quotation accepted by the email provider."})

    @action(detail=True, methods=["post"], url_path="remove-document")
    def remove_document(self, request, reference=None):
        quote = services.remove_client_quotation_document(
            request.user, self.get_object()
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=quote.pk)).data)


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = m.PurchaseOrder.objects.select_related(
        "client", "created_by"
    ).prefetch_related("items__item")
    serializer_class = z.PurchaseOrderSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["reference", "client_reference", "client_name", "items__item_name"]
    ordering_fields = ["received_at", "urgency", "status", "client_name", "created_at"]
    ordering = ["-received_at", "-urgency", "status"]
    http_method_names = ["get", "post", "head", "options"]
    action_permissions = {
        "status": "procurement.change_purchaseorderstatus",
        "attachment": "procurement.download_purchaseorder",
    }

    def create(self, request):
        payload = {key: request.data.get(key) for key in request.data}
        if isinstance(payload.get("items"), str):
            try:
                payload["items"] = json.loads(payload["items"])
            except json.JSONDecodeError:
                return Response({"items": ["Submit valid item details."]}, status=400)
        form = z.PurchaseOrderInput(data=payload)
        form.is_valid(raise_exception=True)
        row = services.create_purchase_order(
            request.user, dict(form.validated_data), request.FILES.get("attachment")
        )
        return Response(
            self.get_serializer(self.get_queryset().get(pk=row.pk)).data, status=201
        )

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        form = z.PurchaseOrderStatusInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.change_purchase_order_status(
            request.user, self.get_object(), **form.validated_data
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=row.pk)).data)

    @action(detail=True, methods=["get"])
    def attachment(self, request, pk=None):
        row = self.get_object()
        if not row.attachment_external_file_id:
            return Response(
                {"detail": "This purchase order has no attachment."}, status=404
            )
        downloaded = services.get_document_storage(
            row.attachment_storage_provider
        ).download(
            external_file_id=row.attachment_external_file_id,
            storage_path=row.attachment_storage_path,
            filename=row.attachment_filename,
            mime_type=row.attachment_mime_type,
        )
        return FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=True,
            filename=downloaded.filename,
        )


class PurchaseViewSet(viewsets.ModelViewSet):
    queryset = m.Purchase.objects.select_related(
        "purchased_from", "purchase_order", "created_by"
    ).prefetch_related("items__item", "items__status_changed_by")
    serializer_class = z.PurchaseSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "purchased_from_name",
        "supplier_document_reference",
        "purchase_order__reference",
        "items__item_name",
    ]
    ordering_fields = ["purchased_at", "purchased_from_name", "created_at"]
    ordering = ["-purchased_at", "-created_at"]
    http_method_names = ["get", "post", "head", "options"]
    action_permissions = {
        "item_status": "procurement.change_purchaseitemstatus",
    }

    def create(self, request):
        form = z.PurchaseInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.create_purchase(request.user, dict(form.validated_data))
        return Response(
            self.get_serializer(self.get_queryset().get(pk=row.pk)).data, status=201
        )

    @action(detail=False, methods=["get"], url_path="party-options")
    def party_options(self, request):
        search = request.query_params.get("search", "")[:100]
        rows = Party.objects.active().filter(display_name__icontains=search)[:50]
        return Response(
            [{"id": str(row.pk), "label": row.display_name} for row in rows]
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"items/(?P<line_id>[^/.]+)/status",
    )
    def item_status(self, request, pk=None, line_id=None):
        form = z.PurchaseItemStatusInput(data=request.data)
        form.is_valid(raise_exception=True)
        line = services.change_purchase_item_status(
            request.user, self.get_object(), line_id, **form.validated_data
        )
        return Response(z.PurchaseItemSerializer(line).data)


class WaybillViewSet(viewsets.ModelViewSet):
    queryset = m.Waybill.objects.select_related(
        "client", "created_by", "purchase_order"
    ).prefetch_related("items__item", "communications")
    serializer_class = z.WaybillSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["reference", "client_name", "delivered_to", "items__item_name"]
    ordering_fields = ["waybill_date", "client_name", "created_at"]
    ordering = ["-waybill_date", "-created_at"]
    lookup_field = "reference"
    lookup_value_regex = "[^/]+"
    http_method_names = ["get", "post", "head", "options"]
    action_permissions = {
        "download": "procurement.communicate_waybill",
        "print_document": "procurement.communicate_waybill",
        "email": "procurement.communicate_waybill",
    }

    def create(self, request):
        form = z.WaybillInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.create_waybill(request.user, dict(form.validated_data))
        return Response(
            self.get_serializer(self.get_queryset().get(pk=row.pk)).data, status=201
        )

    def _document_response(self, request, method, attachment):
        row = self.get_object()
        downloaded = services.download_waybill(row)
        services.record_waybill_communication(request.user, row, method)
        response = FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=attachment,
            filename=downloaded.filename,
        )
        response["Cache-Control"] = "private, no-store"
        return response

    @action(detail=True, methods=["get"])
    def download(self, request, reference=None):
        return self._document_response(request, "DOWNLOAD", True)

    @action(detail=True, methods=["get"], url_path="print")
    def print_document(self, request, reference=None):
        return self._document_response(request, "PRINT", False)

    @action(detail=True, methods=["post"])
    def email(self, request, reference=None):
        form = z.WaybillEmailInput(data=request.data)
        form.is_valid(raise_exception=True)
        services.email_waybill(request.user, self.get_object(), form.validated_data)
        return Response({"detail": "Waybill accepted by the email provider."})


class ItemVerdictViewSet(viewsets.ModelViewSet):
    queryset = m.ItemVerdict.objects.select_related(
        "waybill_item__waybill", "item", "supplier", "created_by", "authorised_by"
    )
    serializer_class = z.ItemVerdictSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "waybill_item__waybill__reference",
        "item__name",
        "supplier_name",
        "client_recipient",
    ]
    ordering_fields = ["occurred_at", "verdict", "created_at"]
    ordering = ["-occurred_at", "-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request):
        form = z.ItemVerdictInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.create_item_verdict(request.user, dict(form.validated_data))
        return Response(
            self.get_serializer(self.get_queryset().get(pk=row.pk)).data, status=201
        )
