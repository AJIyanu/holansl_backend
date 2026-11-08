from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import SalesReceipt, ReceiptEmail
from .receipts import access, create_receipt
from .invoice_documents import render_invoice_pdf
from .party_ledgers import eligible_parties
from .receipt_email import send_receipt


class ReceiptSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    reversed = serializers.SerializerMethodField()

    def get_reversed(self, obj):
        return hasattr(obj.transaction, "reversal")

    class Meta:
        model = SalesReceipt
        exclude = ["submission", "request_key"]


class EmailSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = ReceiptEmail
        fields = [
            "id",
            "destination",
            "subject",
            "message",
            "status",
            "actor_name",
            "created_at",
            "completed_at",
        ]


class ReceiptListView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, party_id):
        access(request.user, "view")
        get_object_or_404(eligible_parties("sales"), pk=party_id)
        rows = SalesReceipt.objects.filter(party_id=party_id).select_related(
            "actor", "transaction__reversal"
        )
        search = serializers.CharField(max_length=100, allow_blank=True).run_validation(
            request.query_params.get("search", "")
        )
        if search:
            rows = rows.filter(
                Q(reference__icontains=search)
                | Q(snapshot__billed_to__icontains=search)
            )
        return self.get_paginated_response(
            ReceiptSerializer(self.paginate_queryset(rows), many=True).data
        )

    def post(self, request, party_id):
        result = create_receipt(
            actor=request.user, party_id=party_id, payload=request.data
        )
        return Response(ReceiptSerializer(result).data, status=201)


class ReceiptView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        access(request.user, "view")
        receipt = get_object_or_404(
            SalesReceipt.objects.select_related("actor", "transaction__reversal"), pk=pk
        )
        data = dict(ReceiptSerializer(receipt).data)
        data["emails"] = (
            EmailSerializer(receipt.emails.select_related("actor"), many=True).data
            if request.user.has_perm("ledger.email_salesreceipt")
            else []
        )
        return Response(data)


class ReceiptPDFView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        access(request.user, "print")
        receipt = get_object_or_404(SalesReceipt, pk=pk)
        response = HttpResponse(
            render_invoice_pdf(receipt.snapshot), content_type="application/pdf"
        )
        response["Content-Disposition"] = f'inline; filename="{receipt.reference}.pdf"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ReceiptEmailView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        access(request.user, "email")
        get_object_or_404(SalesReceipt, pk=pk)
        return Response(
            EmailSerializer(
                send_receipt(actor=request.user, receipt_id=pk, payload=request.data)
            ).data
        )
