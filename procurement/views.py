"""Staff-only procurement APIs with explicit actions and record scope."""

import hashlib
import json
import uuid

from django.db import transaction
from django.http import FileResponse
from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from drf_spectacular.utils import extend_schema

from holansladminservices.pagination import StandardPagination
from . import models as m, serializers as z, services as s, tracking, documents


class Conflict(APIException):
    status_code = 409
    default_detail = "Idempotency key was already used for another operation."


class LedgerUnavailable(APIException):
    status_code = 503
    default_detail = tracking.FINANCE["reason"]
    default_code = "ledger_unavailable"


class ProcurementPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if (
            not user
            or not user.is_authenticated
            or not user.is_active
            or not (user.is_staff or user.is_superuser)
        ):
            return False
        if not user.has_perm("procurement.view_procurementrequest"):
            return False
        model = view.model
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return user.has_perm(f"procurement.view_{model._meta.model_name}")
        if not user.has_perm("procurement.operate_procurementrequest"):
            return False
        verb = "add" if view.action == "create" else "change"
        permission = getattr(view, "action_permissions", {}).get(
            view.action, f"{verb}_{model._meta.model_name}"
        )
        return user.has_perm("procurement." + permission)


PATHS = {
    m.ProcurementRequest: "",
    m.RequestItem: "request",
    m.SupplierQuotation: "request",
    m.ClientQuotation: "request",
    m.ClientQuotationRevision: "quotation__request",
    m.QuotationIssue: "revision__quotation__request",
    m.ClientPurchaseOrder: "request",
    m.FulfilmentAttempt: "item__request",
    m.SupplierPurchase: "request",
    m.GoodsReceipt: "purchase__request",
    m.Delivery: "request",
    m.DeliveryOutcome: "delivery__request",
    m.DeliveryOutcomeLine: "outcome__delivery__request",
    m.RejectionAction: "rejection__outcome__delivery__request",
    m.ProcurementDocument: "request",
    m.ItemTrackingEvent: "item__request",
    m.ProcurementCost: "item__request",
    m.FinanceRequest: "request",
    m.ProcurementTaskLink: "item__request",
}


def request_for(obj):
    path = PATHS[type(obj)]
    for part in path.split("__") if path else []:
        obj = getattr(obj, part)
    return obj


def canonical(value):
    if hasattr(value, "pk"):
        return str(value.pk)
    if hasattr(value, "chunks"):
        from crm.document_services import calculate_document_checksum

        return {"filename": value.name, "checksum": calculate_document_checksum(value)}
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return str(value)


class ResourceViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    action_permissions = {}
    filter_backends = []

    def get_serializer_class(self):
        if self.action == "create" and hasattr(self, "input_serializer"):
            return self.input_serializer
        return z.READ[self.model]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        path = PATHS[self.model]
        qs = self.model.objects.filter(
            **{
                f"{path}__in" if path else "pk__in": s.visible_requests(
                    self.request.user
                )
            }
        )
        if path:
            qs = qs.select_related(path)
        # Preload reverse children used by read serializers.
        nested = {
            m.ProcurementRequest: ["items"],
            m.SupplierQuotation: ["lines"],
            m.ClientQuotation: ["revisions__lines", "revisions__issues"],
            m.ClientQuotationRevision: ["lines", "issues"],
            m.ClientPurchaseOrder: ["lines"],
            m.SupplierPurchase: ["lines"],
            m.GoodsReceipt: ["lines"],
            m.Delivery: ["lines"],
            m.DeliveryOutcome: ["lines"],
            m.FinanceRequest: ["lines"],
        }
        qs = qs.prefetch_related(*nested.get(self.model, []))
        query = self.request.query_params
        root = path + "__" if path else ""
        for param, field in [
            ("request", root + "id"),
            ("client", root + "client_id"),
            ("owner", root + "owner_id"),
        ]:
            if query.get(param):
                try:
                    value = uuid.UUID(query[param])
                except ValueError:
                    raise ValidationError({param: "A UUID is required."})
                qs = qs.filter(**{field: value})
        if query.get("item"):
            try:
                value = uuid.UUID(query["item"])
            except ValueError:
                raise ValidationError({"item": "A UUID is required."})
            if any(f.name == "item" for f in self.model._meta.fields):
                qs = qs.filter(item_id=value)
            elif self.model is m.RequestItem:
                qs = qs.filter(pk=value)
        if query.get("search"):
            if self.model is m.RequestItem:
                qs = qs.filter(description__icontains=query["search"])
            elif self.model is m.ProcurementRequest:
                qs = qs.filter(
                    Q(number__icontains=query["search"])
                    | Q(client_name__icontains=query["search"])
                )
        if query.get("kind") and self.model in {
            m.ItemTrackingEvent,
            m.RejectionAction,
            m.FinanceRequest,
            m.ProcurementCost,
        }:
            qs = qs.filter(kind=query["kind"])
        return qs

    def mutate(self, serializer_class, callback, status=200):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            key = uuid.UUID(self.request.headers.get("Idempotency-Key", ""))
        except (ValueError, TypeError):
            raise ValidationError(
                {
                    "Idempotency-Key": "Supply a unique UUID header for this operation; reuse it only for identical retries."
                }
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "path": self.request.path,
                    "method": self.request.method,
                    "data": canonical(data),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with transaction.atomic():
            # get_or_create's unique key serializes concurrent retries, including different users.
            operation, created = m.Operation.objects.get_or_create(
                key=key,
                defaults={
                    **s.stamp(self.request.user),
                    "fingerprint": fingerprint,
                    "result": {},
                },
            )
            if not created:
                if (
                    operation.created_by_id != self.request.user.id
                    or operation.fingerprint != fingerprint
                ):
                    raise Conflict()
                result = operation.result
                if (
                    not s.visible_requests(self.request.user)
                    .filter(pk=result["request"])
                    .exists()
                ):
                    raise NotFound()
                return Response(result["body"], status=result["status"])
            obj = callback(data)
            body = json.loads(JSONRenderer().render(z.READ[type(obj)](obj).data))
            root = request_for(obj)
            operation.result = {"body": body, "status": status, "request": str(root.id)}
            operation.save(update_fields=["result"])
        return Response(body, status=status)


class CreateResourceViewSet(ResourceViewSet):
    def create(self, request, *args, **kwargs):
        return self.mutate(
            self.input_serializer, lambda data: self.service(request.user, data), 201
        )


def resource(name, model, input_serializer=None, service=None):
    attrs = {"model": model, "queryset": model.objects.none()}
    base = ResourceViewSet
    if service:
        attrs.update(input_serializer=input_serializer, service=staticmethod(service))
        base = CreateResourceViewSet
    return type(name, (base,), attrs)


class RequestViewSet(
    resource("RequestBase", m.ProcurementRequest, z.RequestInput, s.create_request)
):
    action_permissions = {"submit": "change_procurementrequest"}

    @extend_schema(request=z.EmptyInput, responses=z.READ[m.ProcurementRequest])
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(z.EmptyInput, lambda _: s.submit_request(request.user, obj))

    @extend_schema(request=z.RequestEditInput, responses=z.READ[m.ProcurementRequest])
    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()
        return self.mutate(
            z.RequestEditInput, lambda data: s.edit_request(request.user, obj, data)
        )


class ItemViewSet(resource("ItemBase", m.RequestItem)):
    action_permissions = {"reorder": "add_procurementrequest"}

    @action(detail=True, methods=["get"])
    def tracker(self, request, pk=None):
        return Response(tracking.tracker(self.get_object()))

    @action(detail=True, methods=["get"], url_path="available-actions")
    def available_actions(self, request, pk=None):
        return Response(tracking.available_actions(self.get_object(), request.user))

    @extend_schema(request=z.ReorderInput, responses=z.READ[m.ProcurementRequest])
    @action(detail=True, methods=["post"])
    def reorder(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.ReorderInput, lambda data: s.reorder(request.user, obj, data), 201
        )


class OfferViewSet(
    resource("OfferBase", m.SupplierQuotation, z.OfferInput, s.create_offer)
):
    @extend_schema(request=z.OfferInput, responses=z.READ[m.SupplierQuotation])
    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.OfferInput,
            lambda data: s.create_offer(request.user, data, previous=obj),
            201,
        )


class QuotationViewSet(
    resource("QuotationBase", m.ClientQuotation, z.QuoteInput, s.create_quote)
):
    @extend_schema(request=z.QuoteInput, responses=z.READ[m.ClientQuotationRevision])
    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.QuoteInput,
            lambda data: s.create_quote(request.user, data, quotation=obj),
            201,
        )


class RevisionViewSet(resource("RevisionBase", m.ClientQuotationRevision)):
    @extend_schema(request=z.IssueInput, responses=z.READ[m.QuotationIssue])
    @action(detail=True, methods=["post"], url_path="record-sent")
    def record_sent(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.IssueInput, lambda data: s.issue_quote(request.user, obj, data), 201
        )


class DeliveryViewSet(
    resource("DeliveryBase", m.Delivery, z.DeliveryInput, s.create_delivery)
):
    action_permissions = {"outcomes": "add_deliveryoutcome"}

    @extend_schema(request=z.EmptyInput, responses=z.READ[m.Delivery])
    @action(detail=True, methods=["post"])
    def dispatch(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(z.EmptyInput, lambda _: s.dispatch(request.user, obj))

    @extend_schema(request=z.OutcomeInput, responses=z.READ[m.DeliveryOutcome])
    @action(detail=True, methods=["post"])
    def outcomes(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.OutcomeInput, lambda data: s.record_outcome(request.user, obj, data), 201
        )


class OutcomeLineViewSet(resource("OutcomeLineBase", m.DeliveryOutcomeLine)):
    action_permissions = {"resolve": "resolve_procurementrequest"}

    @extend_schema(request=z.RejectionInput, responses=z.READ[m.RejectionAction])
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        obj = self.get_object()
        return self.mutate(
            z.RejectionInput,
            lambda data: s.resolve_rejection(request.user, obj, data),
            201,
        )


class DocumentViewSet(
    resource("DocumentBase", m.ProcurementDocument, z.DocumentInput, documents.upload)
):
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        if not request.user.has_perm("procurement.download_procurementdocument"):
            raise PermissionDenied()
        obj = self.get_object()
        file = documents.download(obj)
        from accounts.models import AuditLog

        AuditLog.objects.create(
            user=request.user,
            event_category="CRUD",
            event_type="READ",
            status="SUCCESS",
            app_label="procurement",
            resource="document",
            action="download",
            object_id=str(obj.pk),
        )
        response = FileResponse(
            file.file_object,
            as_attachment=True,
            filename=obj.filename,
            content_type=obj.mime_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class FinanceViewSet(
    resource("FinanceBase", m.FinanceRequest, z.FinanceInput, s.create_finance_request)
):
    @extend_schema(request=z.EmptyInput)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        self.get_object()
        raise LedgerUnavailable()


class CapabilityViewSet(viewsets.ViewSet):
    permission_classes = [ProcurementPermission]
    model = m.ProcurementRequest

    def list(self, request):
        return Response(
            {
                "ledger": tracking.FINANCE,
                "replacement_attempts": True,
                "private_documents": True,
                "client_reorders": True,
                "automatic_external_sending": False,
                "numbering": "Unique UUID references; business numbering policy pending.",
            }
        )


PurchaseOrderViewSet = resource(
    "PurchaseOrderViewSet", m.ClientPurchaseOrder, z.POInput, s.create_po
)
PurchaseViewSet = resource(
    "PurchaseViewSet", m.SupplierPurchase, z.PurchaseInput, s.create_purchase
)
ReceiptViewSet = resource(
    "ReceiptViewSet", m.GoodsReceipt, z.ReceiptInput, s.create_receipt
)
CostViewSet = resource("CostViewSet", m.ProcurementCost, z.CostInput, s.create_cost)
TaskLinkViewSet = resource(
    "TaskLinkViewSet", m.ProcurementTaskLink, z.TaskLinkInput, s.link_task
)
AttemptViewSet = resource("AttemptViewSet", m.FulfilmentAttempt)
OutcomeViewSet = resource("OutcomeViewSet", m.DeliveryOutcome)
EventViewSet = resource("EventViewSet", m.ItemTrackingEvent)
RejectionViewSet = resource("RejectionViewSet", m.RejectionAction)
