"""Audited setup. No command automatically creates live balances."""

import calendar
from datetime import date
from django.db import transaction
from ledger import models as m
from .common import require, make, check, event, version, Conflict


@transaction.atomic
def create_year(user, year):
    require(user, "add_financialyear")
    check(1900 <= year <= 2200, "Financial year is out of range.")
    check(
        not m.FinancialYear.objects.filter(year=year).exists(),
        "Financial year already exists.",
    )
    obj = make(
        m.FinancialYear, user, year=year, start=date(year, 1, 1), end=date(year, 12, 31)
    )
    for month in range(1, 13):
        make(
            m.AccountingPeriod,
            user,
            year=obj,
            start=date(year, month, 1),
            end=date(year, month, calendar.monthrange(year, month)[1]),
        )
    event(user, "year_created", year=year)
    return obj


@transaction.atomic
def period_action(user, period, expected_version, closed, reason):
    require(user, "close_accountingperiod" if closed else "reopen_accountingperiod")
    check(reason.strip(), "A reason is required.")
    period = m.AccountingPeriod.objects.select_for_update().get(pk=period.pk)
    version(period, expected_version)
    if period.closed == closed:
        raise Conflict("Period is already in the requested state.")
    period.closed = closed
    period.version += 1
    period.save(update_fields=["closed", "version"])
    event(
        user,
        "period_closed" if closed else "period_reopened",
        reason=reason,
        period=period.pk,
    )
    return period


@transaction.atomic
def create_setup(user, model, data):
    require(user, "add_" + model._meta.model_name)
    data = dict(data)
    if model is m.Currency:
        check(
            len(data["code"]) == 3
            and data["code"].isalpha()
            and data["code"].isupper(),
            "Use a three-letter uppercase currency code.",
        )
    elif model is m.LedgerAccount:
        check(not data.get("parent") or data["parent"].active, "Parent is inactive.")
        check(
            not data.get("parent")
            or not m.JournalLine.objects.filter(
                account__account=data["parent"]
            ).exists(),
            "A posted account cannot become a parent.",
        )
    elif model is m.AccountCurrency:
        check(
            data["currency"].active and data["account"].active,
            "Account and currency must be active.",
        )
    elif model is m.CashAccount:
        check(
            data["account"].account.classification == "ASSET",
            "A bank/cash account must be an asset.",
        )
    elif model is m.PostingPolicyVersion:
        expected = {
            "receivable": "ASSET",
            "payable": "LIABILITY",
            "deferred_revenue": "LIABILITY",
            "revenue": "INCOME",
            "advance": "ASSET",
            "opening_equity": "EQUITY",
        }
        check(
            len({data[k].pk for k in expected}) == len(expected),
            "Control mappings must use distinct accounts.",
        )
        for field, kind in expected.items():
            check(
                data[field].classification == kind and data[field].active,
                f"{field} must be an active {kind.lower()} account.",
            )
        data["recognition"] = "ACCEPTANCE"
        data["self_approval"] = True
    elif model is m.TaxPolicyVersion:
        check(
            bool(data["verified_reference"].strip()),
            "Record the tax applicability/evidence reference.",
        )
        check(
            data["liability_account"].classification == "LIABILITY",
            "Tax collection uses a liability account.",
        )
        check(
            data["treatment"] != "TAX_CREDIT"
            or data.get("withheld_account")
            and data["withheld_account"].classification == "ASSET",
            "Tax credit treatment needs an asset account.",
        )
    obj = make(model, user, **data)
    obj.full_clean(exclude=["created_by"])
    event(user, "configuration_created", model=model._meta.model_name, object=obj.pk)
    return obj


@transaction.atomic
def update_setup(user, obj, data):
    require(user, "change_" + obj._meta.model_name)
    allowed = {
        m.Currency: {"name", "symbol", "active"},
        m.LedgerAccount: {"name", "active"},
        m.CashAccount: {"name", "active"},
        m.TaxType: {"name", "authority"},
    }.get(type(obj), set())
    data = dict(data)
    reason = data.pop("reason", "")
    check(reason.strip(), "Configuration change reason is required.")
    check(
        data and set(data) <= allowed,
        "Only descriptive fields and active status may change; create a new policy for accounting changes.",
    )
    obj = type(obj).objects.select_for_update().get(pk=obj.pk)
    for field, value in data.items():
        setattr(obj, field, value)
    obj.full_clean(exclude=["created_by"])
    obj.save(update_fields=list(data))
    event(
        user,
        "configuration_updated",
        reason=reason,
        model=obj._meta.model_name,
        object=obj.pk,
        changed=data,
    )
    return obj
