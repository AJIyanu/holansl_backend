from django.http import HttpResponse
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import SalesInvoice, SalesInvoiceItem, InvoiceRevision, InvoiceEmail
from .invoices import access, save_invoice, invoice_action, invoice_pricing
from .invoice_documents import render_invoice_pdf
from .invoice_email import send_invoice
from .party_ledgers import eligible_parties


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesInvoiceItem
        fields = [
            "id",
            "position",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "amount",
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    items = ItemSerializer(many=True, read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )
    updated_by_name = serializers.CharField(
        source="updated_by.username", read_only=True
    )
    confirmed = serializers.SerializerMethodField()

    def get_confirmed(self, obj):
        return obj.confirmed_revision_id is not None

    class Meta:
        model = SalesInvoice
        fields = [
            "tax_rate",
            "id",
            "party",
            "date",
            "currency",
            "reference",
            "billed_to",
            "billing_address",
            "recipient_email",
            "notes",
            "subtotal",
            "version",
            "confirmed",
            "delivered_at",
            "posted_transaction",
            "items",
            "created_by_name",
            "updated_by_name",
            "created_at",
            "updated_at",
        ]


class EmailSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    invoice_version = serializers.IntegerField(
        source="revision.version", read_only=True
    )

    class Meta:
        model = InvoiceEmail
        fields = [
            "id",
            "destination",
            "subject",
            "message",
            "status",
            "actor_name",
            "invoice_version",
            "created_at",
            "completed_at",
        ]


class InvoiceListView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, party_id):
        access(request.user, "view")
        party = get_object_or_404(eligible_parties("sales"), pk=party_id)
        rows = (
            SalesInvoice.objects.filter(party=party)
            .select_related("created_by", "updated_by")
            .prefetch_related("items")
        )
        search = serializers.CharField(max_length=100, allow_blank=True).run_validation(
            request.query_params.get("search", "")
        )
        if search:
            rows = rows.filter(
                Q(reference__icontains=search)
                | Q(billed_to__icontains=search)
                | Q(items__description__icontains=search)
            ).distinct()
        status = serializers.ChoiceField(
            choices=["all", "not-delivered", "delivered"]
        ).run_validation(request.query_params.get("status", "all"))
        if status != "all":
            rows = rows.filter(delivered_at__isnull=status == "not-delivered")
        response = self.get_paginated_response(
            InvoiceSerializer(self.paginate_queryset(rows), many=True).data
        )
        address = party.addresses.order_by("-is_primary", "id").first()
        email = (
            party.contact_methods.filter(method_type="EMAIL", is_active=True)
            .order_by("-is_primary", "id")
            .first()
        )
        response.data["defaults"] = {
            "billed_to": party.display_name,
            "billing_address": ", ".join(
                filter(
                    None,
                    [
                        getattr(address, k, "")
                        for k in [
                            "line_1",
                            "line_2",
                            "city",
                            "state_region",
                            "postal_code",
                            "country_code",
                        ]
                    ],
                )
            ),
            "recipient_email": email.value if email else "",
        }
        return response

    def post(self, request, party_id):
        access(request.user, "add")
        get_object_or_404(eligible_parties("sales"), pk=party_id)
        invoice = save_invoice(
            actor=request.user, party_id=party_id, payload=request.data
        )
        return Response(InvoiceSerializer(invoice).data, status=201)


class InvoiceView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        access(request.user, "view")
        invoice = get_object_or_404(
            SalesInvoice.objects.select_related(
                "created_by", "updated_by"
            ).prefetch_related("items"),
            pk=pk,
        )
        data = dict(InvoiceSerializer(invoice).data)
        data["company"] = dict(settings.LEDGER_DOCUMENT_COMPANY)
        data["pricing"] = invoice_pricing(invoice)
        data["revisions"] = list(
            invoice.revisions.order_by("-version").values(
                "id", "version", "created_at", "actor__username"
            )
        )
        data["emails"] = (
            EmailSerializer(
                InvoiceEmail.objects.filter(revision__invoice=invoice).select_related(
                    "actor", "revision"
                ),
                many=True,
            ).data
            if request.user.has_perm("ledger.email_salesinvoice")
            else []
        )
        return Response(data)

    def patch(self, request, pk):
        access(request.user, "change")
        invoice = get_object_or_404(SalesInvoice, pk=pk)
        result = save_invoice(
            actor=request.user,
            party_id=invoice.party_id,
            invoice_id=pk,
            payload=request.data,
        )
        return Response(InvoiceSerializer(result).data)


class InvoiceActionView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        access(request.user, action)
        get_object_or_404(SalesInvoice, pk=pk)
        if action == "email":
            result = send_invoice(
                actor=request.user, invoice_id=pk, payload=request.data
            )
            return Response(EmailSerializer(result).data, status=200)
        return Response(
            InvoiceSerializer(
                invoice_action(
                    actor=request.user,
                    invoice_id=pk,
                    action=action,
                    payload=request.data,
                )
            ).data
        )


class InvoicePDFView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        access(request.user, "print")
        invoice = get_object_or_404(SalesInvoice, pk=pk)
        revision_id = request.query_params.get("revision")
        if revision_id:
            revision = get_object_or_404(
                InvoiceRevision,
                invoice=invoice,
                pk=serializers.UUIDField().run_validation(revision_id),
            )
        else:
            if not invoice.confirmed_revision_id:
                raise serializers.ValidationError(
                    "Confirm this invoice before printing it."
                )
            revision = invoice.confirmed_revision
        response = HttpResponse(
            render_invoice_pdf(revision.snapshot), content_type="application/pdf"
        )
        response["Content-Disposition"] = f'inline; filename="{invoice.reference}.pdf"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
