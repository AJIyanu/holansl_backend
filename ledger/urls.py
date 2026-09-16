from django.urls import path
from rest_framework.routers import DefaultRouter
from ledger import models as m, views as v

router = DefaultRouter()
for route, kind in [
    ("invoices", "INVOICE"),
    ("receipts", "RECEIPT"),
    ("payments", "PAYMENT"),
    ("supplier-bills", "SUPPLIER_BILL"),
    ("expenses", "EXPENSE"),
    ("advances", "ADVANCE"),
    ("advance-clearances", "CLEARANCE"),
    ("credit-notes", "CREDIT"),
    ("withholdings", "WITHHOLDING"),
    ("tax-remittances", "TAX_REMITTANCE"),
    ("manual-journals", "MANUAL"),
    ("opening-balances", "OPENING"),
]:
    view = type(
        kind.title().replace("_", "") + "ViewSet",
        (v.FinancialView,),
        {"kind": kind, "__module__": __name__},
    )
    router.register(route, view, basename="ledger-" + route)
for route, model in [
    ("currencies", m.Currency),
    ("accounts", m.LedgerAccount),
    ("account-currencies", m.AccountCurrency),
    ("cash-accounts", m.CashAccount),
    ("posting-policies", m.PostingPolicyVersion),
    ("tax-types", m.TaxType),
    ("tax-policies", m.TaxPolicyVersion),
]:
    router.register(
        route,
        type(
            model.__name__ + "View",
            (v.SetupView,),
            {"model": model, "__module__": __name__},
        ),
        basename="ledger-" + route,
    )
for route, view in [
    ("financial-years", v.YearView),
    ("periods", v.PeriodView),
    ("allocations", v.AllocationView),
    ("client-statements", v.StatementView),
    ("withholding-authorizations", v.AuthorizationView),
    ("approval-requests", v.ApprovalView),
    ("finance-submissions", v.SubmissionView),
    ("documents", v.DocumentView),
    ("journals", v.JournalView),
]:
    router.register(route, view, basename="ledger-" + route)
urlpatterns = [
    path(name + "/", v.UtilityView.as_view({method: action}), name="ledger-" + name)
    for name, method, action in [
        ("capabilities", "get", "capabilities"),
        ("approval-routing", "post", "approval_routing"),
        ("client-codes", "post", "client_codes"),
        ("recognize-revenue", "post", "recognize_revenue"),
        ("tax-reports", "get", "tax_reports"),
        ("trial-balance", "get", "trial_balance"),
    ]
] + router.urls
