import json
from django.db.models import Q, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, MethodNotAllowed
from crm.storage import get_document_storage, DocumentStorageError
from .models import (
    StaffLedgerEntry as Entry,
    StaffLedgerSelection,
    StaffRetirement,
    StaffRetirementDecision,
    StaffLedgerDocument,
    StaffStatement,
)
from .staff_ledger import (
    access,
    eligible_staff,
    staff_name,
    balance,
    post_money,
    submit_retirement,
    decide_retirement,
)
from .staff_reports import PeriodInput, report, statement


def staff_data(user):
    return (
        {
            "id": str(user.pk),
            "name": staff_name(user),
            "username": user.username,
            "is_active": user.is_active,
        }
        if user
        else None
    )


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffLedgerDocument
        fields = [
            "id",
            "filename",
            "mime_type",
            "size_bytes",
            "item_position",
            "created_at",
        ]


class EntrySerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()

    def get_staff_name(self, obj):
        return staff_name(obj.staff)

    actor_name = serializers.CharField(source="actor.username", read_only=True)
    account_name = serializers.CharField(
        source="cash_entry.account.name", read_only=True, default=""
    )
    documents = DocumentSerializer(many=True, read_only=True)
    retirement_id = serializers.UUIDField(
        source="retirement_decision.retirement_id", read_only=True, allow_null=True
    )

    class Meta:
        model = Entry
        exclude = ["payload", "request_key"]


class DecisionSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = StaffRetirementDecision
        exclude = ["payload", "request_key"]


class RetirementSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()

    def get_staff_name(self, obj):
        return staff_name(obj.advance.staff)

    actor_name = serializers.CharField(source="actor.username", read_only=True)
    currency = serializers.CharField(source="advance.currency", read_only=True)
    staff_id = serializers.UUIDField(source="advance.staff_id", read_only=True)
    advance_description = serializers.CharField(
        source="advance.description", read_only=True
    )
    decision = DecisionSerializer(read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)

    class Meta:
        model = StaffRetirement
        exclude = ["payload", "request_key"]


class StatementSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = StaffStatement
        exclude = ["request_key"]


def body(request):
    if "payload" not in request.data:
        return request.data
    try:
        value = json.loads(request.data["payload"])
        if isinstance(value, dict):
            return value
    except (TypeError, ValueError):
        pass
    raise ValidationError("Invalid staff ledger details.")


def entries():
    return Entry.objects.select_related(
        "actor", "staff", "cash_entry__account", "retirement_decision"
    ).prefetch_related("documents")


def retirements():
    return StaffRetirement.objects.select_related(
        "actor", "advance__staff", "decision__actor"
    ).prefetch_related("documents")


class StaffView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, resource="selection", staff_id=None, pk=None):
        access(
            request.user,
            "download"
            if resource == "download"
            else "print"
            if resource in ["statements", "statement"]
            else "view",
        )
        if resource == "selection":
            row = (
                StaffLedgerSelection.objects.filter(user=request.user)
                .select_related("staff")
                .first()
            )
            return Response({"selected_staff": staff_data(row.staff if row else None)})
        if resource == "staff":
            search = serializers.CharField(
                max_length=100, allow_blank=True
            ).run_validation(request.query_params.get("search", ""))
            rows = eligible_staff().filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
            return self.get_paginated_response(
                [staff_data(u) for u in self.paginate_queryset(rows)]
            )
        if resource == "entry":
            return Response(EntrySerializer(get_object_or_404(entries(), pk=pk)).data)
        if resource == "retirement":
            row = get_object_or_404(retirements(), pk=pk)
            data = dict(RetirementSerializer(row).data)
            data["remaining_advance"] = str(balance(row.advance)[0])
            return Response(data)
        if resource == "statement":
            return Response(
                StatementSerializer(
                    get_object_or_404(
                        StaffStatement.objects.select_related("actor"), pk=pk
                    )
                ).data
            )
        if resource == "download":
            row = get_object_or_404(StaffLedgerDocument, pk=pk)
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
        if resource == "decision":
            raise MethodNotAllowed(request.method)
        staff = get_object_or_404(eligible_staff(), pk=staff_id)
        if resource == "entries":
            form = PeriodInput(data=request.query_params)
            form.is_valid(raise_exception=True)
            d = form.validated_data
            rows = (
                entries()
                .filter(
                    staff=staff,
                    currency=d["currency"],
                    date__range=(d["date_from"], d["date_to"]),
                )
                .order_by("-date", "-created_at", "-id")
            )
            response = self.get_paginated_response(
                EntrySerializer(self.paginate_queryset(rows), many=True).data
            )
            response.data["summary"] = report(staff, d, include_transactions=False)[
                "summary"
            ]
            return response
        if resource == "choices":
            currency = serializers.ChoiceField(
                choices=["NGN", "USD", "GBP"]
            ).run_validation(request.query_params.get("currency", "NGN"))
            mode = serializers.ChoiceField(
                choices=["advance", "loan", "reimbursement", "retire"]
            ).run_validation(request.query_params.get("mode", "advance"))
            search = serializers.CharField(
                max_length=100, allow_blank=True
            ).run_validation(request.query_params.get("search", ""))
            rows = (
                entries()
                .filter(
                    staff=staff,
                    currency=currency,
                    kind="PAY",
                    category="LOAN" if mode == "loan" else "ADVANCE",
                )
                .filter(
                    Q(description__icontains=search) | Q(reference__icontains=search)
                )
            )
            if mode == "retire":
                rows = rows.exclude(
                    retirements__in=StaffRetirement.objects.filter(
                        Q(decision__isnull=True) | Q(decision__approved=True)
                    )
                )
            # Aggregate settlement balances in the page query, not once per choice.
            rows = rows.annotate(
                repaid_receivable=Coalesce(
                    Sum("settlements__receivable_delta"),
                    Value(0),
                    output_field=DecimalField(max_digits=24, decimal_places=2),
                ),
                settled_payable=Coalesce(
                    Sum("settlements__payable_delta"),
                    Value(0),
                    output_field=DecimalField(max_digits=24, decimal_places=2),
                ),
            )
            result = []
            for row in self.paginate_queryset(
                rows.order_by("-date", "-created_at", "-id")
            ):
                data = dict(EntrySerializer(row).data)
                data["remaining"] = str(row.receivable_delta + row.repaid_receivable)
                data["reimbursement_due"] = str(row.payable_delta + row.settled_payable)
                result.append(data)
            return self.get_paginated_response(result)
        if resource == "retirements":
            rows = retirements().filter(advance__staff=staff)
            return self.get_paginated_response(
                RetirementSerializer(self.paginate_queryset(rows), many=True).data
            )
        if resource == "statements":
            rows = (
                StaffStatement.objects.filter(staff=staff)
                .select_related("actor")
                .defer("snapshot")
            )
            page = self.paginate_queryset(rows)
            return self.get_paginated_response(
                [
                    {
                        "id": str(r.pk),
                        "date_from": str(r.date_from),
                        "date_to": str(r.date_to),
                        "currency": r.currency,
                        "created_at": r.created_at,
                    }
                    for r in page
                ]
            )

    def put(self, request, resource="selection", **kwargs):
        access(request.user)
        if resource != "selection":
            raise MethodNotAllowed(request.method)
        identity = serializers.UUIDField().run_validation(request.data.get("staff_id"))
        staff = get_object_or_404(eligible_staff(), pk=identity)
        StaffLedgerSelection.objects.update_or_create(
            user=request.user, defaults={"staff": staff}
        )
        return Response({"selected_staff": staff_data(staff)})

    def post(self, request, resource="selection", staff_id=None, pk=None):
        access(request.user)
        if resource == "entries":
            row = post_money(
                actor=request.user,
                staff_id=staff_id,
                payload=body(request),
                files=request.FILES,
            )
            return Response(EntrySerializer(row).data, status=201)
        if resource == "retirements":
            row = submit_retirement(
                actor=request.user,
                staff_id=staff_id,
                payload=body(request),
                files=request.FILES,
            )
            return Response(RetirementSerializer(row).data, status=201)
        if resource == "decision":
            access(request.user, "approve")
            get_object_or_404(StaffRetirement, pk=pk)
            return Response(
                DecisionSerializer(
                    decide_retirement(actor=request.user, pk=pk, payload=request.data)
                ).data,
                status=201,
            )
        if resource == "statements":
            return Response(
                StatementSerializer(
                    statement(
                        actor=request.user, staff_id=staff_id, payload=request.data
                    )
                ).data,
                status=201,
            )
        raise MethodNotAllowed(request.method)
