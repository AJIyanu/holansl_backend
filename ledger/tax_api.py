import csv
import json
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, MethodNotAllowed
from crm.models import Party
from crm.storage import get_document_storage, DocumentStorageError
from .models import TaxEntry, TaxRequest, TaxDecision, TaxDocument, TaxPolicy
from .tax_operations import access, prepare, decide, balances
from .tax_posting import totals, FIELDS, policy_rate
from .staff_reports import PeriodInput
from .idempotency import lock_submission
from .operations import require
from .invoices import today


class EntrySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    party_name = serializers.CharField(
        source="party.display_name", read_only=True, default=""
    )

    class Meta:
        model = TaxEntry
        fields = "__all__"


class DecisionSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = TaxDecision
        exclude = ["payload", "request_key"]


class RequestSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)
    decision = DecisionSerializer(read_only=True)
    document = serializers.SerializerMethodField()

    def get_document(self, obj):
        if not hasattr(obj, "document"):
            return None
        return {"id": str(obj.document.pk), "filename": obj.document.filename}

    class Meta:
        model = TaxRequest
        exclude = ["request_key"]


class PolicyInput(serializers.Serializer):
    request_key = serializers.UUIDField()
    party_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    kind = serializers.ChoiceField(choices=["VAT", "WHT"])
    rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=Decimal("99.99")
    )
    enabled = serializers.BooleanField(default=True)
    effective_date = serializers.DateField()
    reason = serializers.CharField(max_length=1000)


@transaction.atomic
def policy(actor, payload):
    access(actor, "configure")
    form = PolicyInput(data=payload)
    form.is_valid(raise_exception=True)
    d = form.validated_data
    lock_submission(d["request_key"])
    lock_submission(uuid5(NAMESPACE_URL, "holan:tax-policy"))
    old = TaxPolicy.objects.filter(request_key=d["request_key"]).first()
    if old:
        if old.actor_id != actor.pk or any(
            str(getattr(old, k)) != str(v) for k, v in d.items()
        ):
            raise ValidationError(
                "Submission identifier was used with different policy details."
            )
        return old
    if d["kind"] == "VAT" and d["party_id"]:
        raise ValidationError(
            "VAT policy is company-wide; client-specific policies apply to WHT."
        )
    if d["party_id"]:
        get_object_or_404(Party, pk=d["party_id"])
    if d["effective_date"] < today():
        raise ValidationError("New tax policy versions cannot rewrite past dates.")
    return TaxPolicy.objects.create(actor=actor, **d)


class TaxView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, resource="entries", pk=None):
        if resource == "rates":
            require(request.user, "ledger.view_salesledger")
            if not any(
                request.user.has_perm(f"ledger.view_{m}")
                for m in ["salesreceipt", "salesinvoice"]
            ):
                from rest_framework.exceptions import PermissionDenied

                raise PermissionDenied()
            date = serializers.DateField().run_validation(
                request.query_params.get("date", str(today()))
            )
            party = serializers.UUIDField(
                required=False, allow_null=True
            ).run_validation(request.query_params.get("party"))
            rate = policy_rate("VAT", date)
            if request.query_params.get("invoice"):
                from .models import SalesInvoice

                identity = serializers.UUIDField().run_validation(
                    request.query_params["invoice"]
                )
                invoice = get_object_or_404(SalesInvoice, pk=identity, party_id=party)
                rate = invoice.tax_rate
            return Response(
                {
                    "vat_rate": str(rate),
                    "withholding_rate": str(policy_rate("WHT", date, party)),
                }
            )
        access(request.user, "export" if resource == "export" else "view")
        if resource in ["entries", "export"]:
            form = PeriodInput(data=request.query_params)
            form.is_valid(raise_exception=True)
            d = form.validated_data
            rows = TaxEntry.objects.filter(currency=d["currency"]).select_related(
                "actor", "party"
            )
            if request.query_params.get("party"):
                party = serializers.UUIDField().run_validation(
                    request.query_params["party"]
                )
                rows = rows.filter(party_id=party)
            tax = serializers.ChoiceField(
                choices=["ALL", "VAT", "WHT", "CIT"]
            ).run_validation(request.query_params.get("tax_type", "ALL"))
            fields = {
                "VAT": [
                    "output_vat",
                    "input_vat",
                    "input_pending",
                    "input_nonrecoverable",
                    "sales_vat_cash",
                    "purchase_vat_cash",
                    "vat_remitted",
                    "input_used",
                ],
                "WHT": ["wht_asset", "wht_liability", "wht_remitted", "wht_used"],
                "CIT": ["cit_assessed", "cit_paid", "wht_used"],
            }
            if tax != "ALL":
                condition = Q()
                for f in fields[tax]:
                    condition |= ~Q(**{f: 0})
                rows = rows.filter(condition)
            opening = totals(rows.filter(date__lt=d["date_from"]))
            closing = totals(rows.filter(date__lte=d["date_to"]))
            period = rows.filter(date__range=(d["date_from"], d["date_to"]))
            if resource == "export":
                response = HttpResponse(content_type="text/csv")
                response["Content-Disposition"] = (
                    'attachment; filename="tax-ledger.csv"'
                )
                response["Cache-Control"] = "private, no-store"
                writer = csv.writer(response)
                writer.writerow(
                    [
                        "Date",
                        "Period",
                        "Currency",
                        "Party",
                        "Kind",
                        "Reference",
                        "Description",
                        *FIELDS,
                    ]
                )

                def safe(s):
                    return (
                        "'" + s
                        if isinstance(s, str)
                        and s.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                        else s
                    )

                for e in period.iterator(chunk_size=1000):
                    writer.writerow(
                        [
                            e.date,
                            e.period,
                            e.currency,
                            safe(e.party.display_name if e.party else ""),
                            e.kind,
                            safe(e.reference),
                            safe(e.description),
                            *[str(getattr(e, f)) for f in FIELDS],
                        ]
                    )
                return response
            response = self.get_paginated_response(
                EntrySerializer(
                    self.paginate_queryset(
                        period.order_by("-date", "-created_at", "-id")
                    ),
                    many=True,
                ).data
            )
            response.data.update(
                opening={k: str(v) for k, v in opening.items()},
                closing={k: str(v) for k, v in closing.items()},
                period_totals={k: str(v) for k, v in totals(period).items()},
                balances={
                    k: str(v) for k, v in balances(d["currency"], d["date_to"]).items()
                },
            )
            return response
        if resource == "requests":
            rows = TaxRequest.objects.select_related(
                "actor", "decision__actor", "document"
            )
            status = serializers.ChoiceField(
                choices=["all", "pending", "approved", "rejected"]
            ).run_validation(request.query_params.get("status", "all"))
            if status == "pending":
                rows = rows.filter(decision__isnull=True)
            elif status != "all":
                rows = rows.filter(decision__approved=status == "approved")
            return self.get_paginated_response(
                RequestSerializer(self.paginate_queryset(rows), many=True).data
            )
        if resource == "request":
            return Response(
                RequestSerializer(
                    get_object_or_404(
                        TaxRequest.objects.select_related(
                            "actor", "decision__actor", "document"
                        ),
                        pk=pk,
                    )
                ).data
            )
        if resource == "policies":
            access(request.user, "configure")
            rows = TaxPolicy.objects.select_related("actor", "party")
            return self.get_paginated_response(
                [
                    {
                        "id": str(r.pk),
                        "party": str(r.party_id) if r.party_id else None,
                        "party_name": r.party.display_name
                        if r.party
                        else "Company default",
                        "kind": r.kind,
                        "rate": str(r.rate),
                        "enabled": r.enabled,
                        "effective_date": str(r.effective_date),
                        "reason": r.reason,
                        "actor_name": r.actor.username,
                    }
                    for r in self.paginate_queryset(rows)
                ]
            )
        if resource == "parties":
            search = serializers.CharField(
                max_length=100, allow_blank=True
            ).run_validation(request.query_params.get("search", ""))
            rows = Party.objects.filter(display_name__icontains=search).order_by(
                "display_name", "pk"
            )
            return self.get_paginated_response(
                [
                    {"id": str(p.pk), "name": p.display_name}
                    for p in self.paginate_queryset(rows)
                ]
            )
        if resource == "sources":
            currency = serializers.ChoiceField(
                choices=["NGN", "USD", "GBP"]
            ).run_validation(request.query_params.get("currency", "NGN"))
            search = serializers.CharField(
                max_length=100, allow_blank=True
            ).run_validation(request.query_params.get("search", ""))
            rows = (
                TaxEntry.objects.filter(currency=currency)
                .exclude(document_key="")
                .filter(
                    Q(reference__icontains=search)
                    | Q(party__display_name__icontains=search)
                )
                .values("document_key")
                .distinct()
                .order_by("document_key")
            )
            result = []
            selected = list(self.paginate_queryset(rows))
            grouped = {item["document_key"]: [] for item in selected}
            for entry in (
                TaxEntry.objects.filter(document_key__in=grouped)
                .select_related("party")
                .order_by("date", "created_at", "id")
            ):
                grouped[entry.document_key].append(entry)
            for item in selected:
                group = grouped[item["document_key"]]
                e = group[0]
                t = {
                    field: sum((getattr(row, field) for row in group), Decimal("0"))
                    for field in [
                        "party_vat",
                        "input_vat",
                        "input_pending",
                        "input_nonrecoverable",
                    ]
                }
                result.append(
                    {
                        "document_key": item["document_key"],
                        "reference": e.reference,
                        "party_name": e.party.display_name if e.party else "",
                        "currency": currency,
                        "outstanding_vat": str(t["party_vat"]),
                        "input_vat": str(
                            t["input_vat"]
                            + t["input_pending"]
                            + t["input_nonrecoverable"]
                        ),
                    }
                )
            return self.get_paginated_response(result)
        if resource == "history":
            access(request.user, "adjust")
            from .tax_backfill import preview

            return Response(preview())
        if resource == "download":
            row = get_object_or_404(TaxDocument, pk=pk)
            try:
                doc = get_document_storage(row.storage_provider).download(
                    external_file_id=row.external_file_id,
                    storage_path=row.storage_path,
                    filename=row.filename,
                    mime_type=row.mime_type,
                )
            except DocumentStorageError:
                raise ValidationError(
                    "Supporting document is temporarily unavailable."
                ) from None
            response = FileResponse(
                doc.file_object,
                as_attachment=True,
                filename=row.filename,
                content_type=row.mime_type,
            )
            response["Cache-Control"] = "private, no-store"
            response["X-Content-Type-Options"] = "nosniff"
            response["Content-Security-Policy"] = "sandbox"
            return response
        raise MethodNotAllowed(request.method)

    def post(self, request, resource="entries", pk=None):
        if resource == "requests":
            try:
                data = (
                    json.loads(request.data["payload"])
                    if "payload" in request.data
                    else request.data
                )
            except (ValueError, TypeError):
                raise ValidationError("Invalid tax request.") from None
            return Response(
                RequestSerializer(
                    prepare(
                        actor=request.user, payload=data, file=request.FILES.get("file")
                    )
                ).data,
                status=201,
            )
        if resource == "decision":
            access(request.user, "approve")
            get_object_or_404(TaxRequest, pk=pk)
            return Response(
                DecisionSerializer(
                    decide(actor=request.user, pk=pk, payload=request.data)
                ).data,
                status=201,
            )
        if resource == "policies":
            return Response(
                {"id": str(policy(request.user, request.data).pk)}, status=201
            )
        raise MethodNotAllowed(request.method)
