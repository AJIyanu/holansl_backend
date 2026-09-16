"""Client authorization and explicit collected/withheld/remitted tax measures."""

from decimal import Decimal
from django.db import transaction
from django.db.models import Q, Sum
from ledger import models as m
from .common import require, check, make, evidence, event, business_date, full_scope


@transaction.atomic
def authorize(user, data):
    require(user, "authorize_clientwithholdingauthorization")
    full_scope(user, "view_clientwithholdingauthorization")
    from crm.models import Party

    Party.objects.select_for_update().get(pk=data["party"].pk)
    start = business_date(data["effective_from"])
    end = data.get("effective_to")
    check(not end or end >= start, "Authorization end precedes start.")
    qs = (
        m.ClientWithholdingAuthorization.objects.filter(
            party=data["party"], tax_type=data["tax_type"]
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start))
        .filter(Q(revoked_from__isnull=True) | Q(revoked_from__gt=start))
    )
    if end:
        qs = qs.filter(effective_from__lte=end)
    check(not qs.exists(), "Overlapping withholding authorization exists.")
    check(data["reason"].strip(), "Authorization reason is required.")
    doc = evidence(user, data["evidence"], data["party"])
    obj = make(
        m.ClientWithholdingAuthorization,
        user,
        party=data["party"],
        tax_type=data["tax_type"],
        effective_from=start,
        effective_to=end,
        evidence=doc,
        reason=data["reason"],
    )
    event(user, "withholding_authorized", reason=data["reason"], authorization=obj.pk)
    return obj


@transaction.atomic
def revoke(user, obj, day, reason):
    full_scope(user, "authorize_clientwithholdingauthorization")
    from crm.models import Party

    Party.objects.select_for_update().get(pk=obj.party_id)
    obj = m.ClientWithholdingAuthorization.objects.select_for_update().get(pk=obj.pk)
    day = business_date(day)
    check(
        reason.strip() and obj.revoked_from is None,
        "Supply a reason for an active authorization.",
    )
    check(day >= obj.effective_from, "Revocation precedes authorization.")
    check(
        not m.WithholdingRecord.objects.filter(
            authorization=obj, record__status="POSTED", record__date__gte=day
        ).exists(),
        "Revocation cannot invalidate posted deductions.",
    )
    obj.revoked_from = day
    obj.save(update_fields=["revoked_from"])
    event(
        user,
        "withholding_revoked",
        reason=reason,
        authorization=obj.pk,
        effective_date=day,
    )
    return obj


def collections(policy, currency, date_from=None, date_to=None):
    qs = m.TaxCollectionAllocation.objects.filter(
        line__tax_policy=policy, allocation__source__record__currency=currency
    ).select_related("allocation")
    if date_from:
        qs = qs.filter(allocation__date__gte=date_from)
    if date_to:
        qs = qs.filter(allocation__date__lte=date_to)
    return sum(
        ((-r.amount if r.allocation.release_of_id else r.amount) for r in qs),
        Decimal(0),
    )


def remitted(policy, currency, date_from=None, date_to=None):
    qs = m.JournalEntry.objects.filter(
        source__payment__purpose="TAX_REMITTANCE",
        source__currency=currency,
        posted_at__isnull=False,
        source__payment__target__tax_remittance__tax_policy=policy,
    ).select_related("source")
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return sum(
        ((-e.source.amount if e.reversal_of_id else e.source.amount) for e in qs),
        Decimal(0),
    )


def remittable(policy, currency):
    return collections(policy, currency) - remitted(policy, currency)


def report(user, currency, date_from, date_to):
    full_scope(user, "view_taxreport")
    check(date_from <= date_to, "Invalid date range.")
    rows = []
    for policy in m.TaxPolicyVersion.objects.select_related("tax_type"):
        lines = m.InvoiceLine.objects.filter(
            tax_policy=policy,
            invoice__record__currency=currency,
            invoice__record__status="POSTED",
            invoice__record__date__range=(date_from, date_to),
        )
        charged = lines.aggregate(n=Sum("tax"))["n"] or Decimal(0)
        credited = m.CreditNoteLine.objects.filter(
            line__tax_policy=policy,
            credit__record__currency=currency,
            credit__record__status="POSTED",
            credit__record__date__range=(date_from, date_to),
        ).aggregate(n=Sum("tax"))["n"] or Decimal(0)
        deduction_movements = (
            m.ClientMovement.objects.filter(
                source__withholding__line__tax_policy=policy,
                account__currency=currency,
                date__range=(date_from, date_to),
            )
            .select_related("source__withholding__line")
            .prefetch_related("source__withholding__remittance_evidence")
        )
        deductions = sum((r.credit - r.debit for r in deduction_movements), Decimal(0))
        collected = collections(policy, currency, date_from, date_to)
        paid = remitted(policy, currency, date_from, date_to)
        rows.append(
            dict(
                policy=str(policy.pk),
                tax_type=policy.tax_type.name,
                treatment=policy.treatment,
                charged=str(charged),
                credited=str(credited),
                net_charged=str(charged - credited),
                collected=str(collected),
                withheld=str(deductions),
                remitted=str(paid),
                collected_unremitted_to_date=str(
                    collections(policy, currency, date_to=date_to)
                    - remitted(policy, currency, date_to=date_to)
                ),
                clients=[
                    dict(
                        client=str(r.source.party_id),
                        invoice=str(r.source.withholding.line.invoice_id),
                        date=str(r.date),
                        kind=r.kind,
                        withheld=str(r.credit - r.debit),
                        reference=r.source.withholding.certificate_reference,
                        remittance_evidence_count=r.source.withholding.remittance_evidence.count(),
                    )
                    for r in deduction_movements
                ],
            )
        )
    return dict(
        currency=currency.code,
        date_from=str(date_from),
        date_to=str(date_to),
        results=rows,
    )
