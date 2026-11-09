import json
from django.http import FileResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from crm.storage import get_document_storage, DocumentStorageError
from .models import PurchaseBill, PurchasePayment, PurchaseReceiptEvidence
from .purchases import access, save_bill, deliver_bill, create_payment
from .purchase_uploads import update_evidence
from .party_ledgers import eligible_parties


class EvidenceSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = PurchaseReceiptEvidence
        fields = [
            "id",
            "version",
            "receipt_number",
            "filename",
            "mime_type",
            "size_bytes",
            "actor_name",
            "created_at",
        ]


class BillSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    reversed = serializers.SerializerMethodField()

    def get_reversed(self, obj):
        return bool(obj.transaction_id and hasattr(obj.transaction, "reversal"))

    class Meta:
        model = PurchaseBill
        fields = "__all__"


class PaymentSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    evidence = EvidenceSerializer(many=True, read_only=True)
    reversed = serializers.SerializerMethodField()

    def get_reversed(self, obj):
        return hasattr(obj.transaction, "reversal")

    class Meta:
        model = PurchasePayment
        exclude = ["submission", "request_key"]


def payload(request):
    if "payload" not in request.data:
        return request.data
    try:
        result = json.loads(request.data["payload"])
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValidationError("Invalid payment or receipt details.") from None


class PurchaseListView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, party_id, kind):
        access(
            request.user,
            "purchasebill" if kind == "bills" else "purchasepayment",
            "view",
        )
        get_object_or_404(eligible_parties("purchase"), pk=party_id)
        search = serializers.CharField(max_length=100, allow_blank=True).run_validation(
            request.query_params.get("search", "")
        )
        if kind == "bills":
            rows = PurchaseBill.objects.filter(party_id=party_id).select_related(
                "actor", "transaction__reversal"
            )
            if search:
                rows = rows.filter(
                    Q(reference__icontains=search)
                    | Q(snapshot__description__icontains=search)
                )
            state = serializers.ChoiceField(
                choices=["all", "delivered", "not-delivered"]
            ).run_validation(request.query_params.get("status", "all"))
            if state != "all":
                rows = rows.filter(delivered_at__isnull=state == "not-delivered")
            serializer = BillSerializer
        else:
            rows = (
                PurchasePayment.objects.filter(party_id=party_id)
                .select_related("actor", "transaction__reversal")
                .prefetch_related("evidence__actor")
            )
            if search:
                rows = rows.filter(
                    Q(evidence__receipt_number__icontains=search)
                    | Q(snapshot__description__icontains=search)
                ).distinct()
            serializer = PaymentSerializer
        return self.get_paginated_response(
            serializer(self.paginate_queryset(rows), many=True).data
        )

    def post(self, request, party_id, kind):
        if kind == "bills":
            return Response(
                BillSerializer(
                    save_bill(
                        actor=request.user, party_id=party_id, payload=request.data
                    )
                ).data,
                status=201,
            )
        result = create_payment(
            actor=request.user,
            party_id=party_id,
            payload=payload(request),
            uploaded_file=request.FILES.get("file"),
        )
        return Response(PaymentSerializer(result).data, status=201)


class PurchaseDetailView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, kind):
        access(
            request.user,
            "purchasebill" if kind == "bills" else "purchasepayment",
            "view",
        )
        if kind == "bills":
            bill = get_object_or_404(
                PurchaseBill.objects.select_related("actor", "transaction__reversal"),
                pk=pk,
            )
            data = dict(BillSerializer(bill).data)
            data["events"] = list(
                bill.events.values(
                    "action", "snapshot", "actor__username", "created_at"
                )
            )
            return Response(data)
        return Response(
            PaymentSerializer(
                get_object_or_404(
                    PurchasePayment.objects.select_related(
                        "actor", "transaction__reversal"
                    ).prefetch_related("evidence__actor"),
                    pk=pk,
                )
            ).data
        )

    def patch(self, request, pk, kind):
        access(
            request.user,
            "purchasebill" if kind == "bills" else "purchasepayment",
            "change",
        )
        if kind == "bills":
            bill = get_object_or_404(PurchaseBill, pk=pk)
            return Response(
                BillSerializer(
                    save_bill(
                        actor=request.user,
                        party_id=bill.party_id,
                        payload=request.data,
                        bill_id=pk,
                    )
                ).data
            )
        get_object_or_404(PurchasePayment, pk=pk)
        return Response(
            EvidenceSerializer(
                update_evidence(
                    actor=request.user,
                    pk=pk,
                    payload=payload(request),
                    uploaded_file=request.FILES.get("file"),
                )
            ).data
        )


class BillDeliveryView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        access(request.user, "purchasebill", "deliver")
        get_object_or_404(PurchaseBill, pk=pk)
        return Response(
            BillSerializer(
                deliver_bill(actor=request.user, pk=pk, payload=request.data)
            ).data
        )


class SupplierReceiptDownloadView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, evidence_id):
        access(request.user, "purchasepayment", "download")
        row = get_object_or_404(PurchaseReceiptEvidence, payment_id=pk, pk=evidence_id)
        if not row.filename:
            raise ValidationError("This receipt revision has no uploaded scan.")
        try:
            document = get_document_storage(row.storage_provider).download(
                external_file_id=row.external_file_id,
                storage_path=row.storage_path,
                filename=row.filename,
                mime_type=row.mime_type,
            )
        except DocumentStorageError:
            raise ValidationError("Receipt scan is temporarily unavailable.") from None
        response = FileResponse(
            document.file_object,
            as_attachment=True,
            filename=row.filename,
            content_type=row.mime_type,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"
        return response
