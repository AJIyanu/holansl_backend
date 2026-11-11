from django.urls import path
from rest_framework.routers import DefaultRouter
from .party_ledgers import (
    PartyLedgerSelectionView,
    PartyLedgerLookupView,
    PartyLedgerTransactionsView,
    PartyOutstandingDocumentsView,
)

from .workspace_api import (
    TransactionChoicesView,
    OperationsView,
    StatementsView,
    StatementView,
    AccountsView,
)

from .invoice_api import InvoiceListView, InvoiceView, InvoiceActionView, InvoicePDFView

from .receipt_api import ReceiptListView, ReceiptView, ReceiptPDFView, ReceiptEmailView

from .purchase_api import (
    PurchaseListView,
    PurchaseDetailView,
    BillDeliveryView,
    SupplierReceiptDownloadView,
)

from .tax_api import TaxView
from .staff_api import StaffView

from .views import CashBookViewSet, CashBookJournalViewSet

router = DefaultRouter()
router.register("cash-book", CashBookViewSet, basename="cash-book")
router.register(
    "cash-book-journal", CashBookJournalViewSet, basename="cash-book-journal"
)
urlpatterns = [
    path("tax/", TaxView.as_view()),
    *[
        path(f"tax/{resource}/", TaxView.as_view(), {"resource": resource})
        for resource in (
            "requests",
            "policies",
            "parties",
            "sources",
            "rates",
            "export",
            "history",
        )
    ],
    path("tax/requests/<uuid:pk>/", TaxView.as_view(), {"resource": "request"}),
    path(
        "tax/requests/<uuid:pk>/decision/", TaxView.as_view(), {"resource": "decision"}
    ),
    path(
        "tax/documents/<uuid:pk>/download/", TaxView.as_view(), {"resource": "download"}
    ),
    path("staff-ledger/", StaffView.as_view()),
    path("staff-ledger/staff/", StaffView.as_view(), {"resource": "staff"}),
    *[
        path(
            f"staff-ledger/staff/<uuid:staff_id>/{resource}/",
            StaffView.as_view(),
            {"resource": resource},
        )
        for resource in ("entries", "choices", "retirements", "statements")
    ],
    *[
        path(
            f"staff-ledger/{plural}/<uuid:pk>/",
            StaffView.as_view(),
            {"resource": resource},
        )
        for plural, resource in (
            ("entries", "entry"),
            ("retirements", "retirement"),
            ("statements", "statement"),
        )
    ],
    path(
        "staff-ledger/retirements/<uuid:pk>/decision/",
        StaffView.as_view(),
        {"resource": "decision"},
    ),
    path(
        "staff-ledger/documents/<uuid:pk>/download/",
        StaffView.as_view(),
        {"resource": "download"},
    ),
    *[
        path(
            f"purchase-{kind}/parties/<uuid:party_id>/",
            PurchaseListView.as_view(),
            {"kind": kind},
        )
        for kind in ("bills", "payments")
    ],
    *[
        path(
            f"purchase-{kind}/<uuid:pk>/", PurchaseDetailView.as_view(), {"kind": kind}
        )
        for kind in ("bills", "payments")
    ],
    path("purchase-bills/<uuid:pk>/deliver/", BillDeliveryView.as_view()),
    path(
        "purchase-payments/<uuid:pk>/evidence/<uuid:evidence_id>/download/",
        SupplierReceiptDownloadView.as_view(),
    ),
    path("sales-receipts/parties/<uuid:party_id>/", ReceiptListView.as_view()),
    path("sales-receipts/<uuid:pk>/", ReceiptView.as_view()),
    path("sales-receipts/<uuid:pk>/pdf/", ReceiptPDFView.as_view()),
    path("sales-receipts/<uuid:pk>/email/", ReceiptEmailView.as_view()),
    path("sales-invoices/parties/<uuid:party_id>/", InvoiceListView.as_view()),
    path("sales-invoices/<uuid:pk>/", InvoiceView.as_view()),
    path("sales-invoices/<uuid:pk>/pdf/", InvoicePDFView.as_view()),
    *[
        path(
            f"sales-invoices/<uuid:pk>/{action}/",
            InvoiceActionView.as_view(),
            {"action": action},
        )
        for action in ("confirm", "deliver", "email")
    ],
    path(
        "party-ledgers/<str:kind>/parties/<uuid:party_id>/choices/",
        TransactionChoicesView.as_view(),
    ),
    path("accounts/", AccountsView.as_view()),
    path("statements/<uuid:pk>/", StatementView.as_view()),
    path(
        "party-ledgers/<str:kind>/parties/<uuid:party_id>/operations/",
        OperationsView.as_view(),
    ),
    path(
        "party-ledgers/<str:kind>/parties/<uuid:party_id>/statements/",
        StatementsView.as_view(),
    ),
    path(
        "party-ledgers/<str:kind>/parties/<uuid:party_id>/outstanding/",
        PartyOutstandingDocumentsView.as_view(),
    ),
    path(
        "party-ledgers/<str:kind>/parties/<uuid:party_id>/transactions/",
        PartyLedgerTransactionsView.as_view(),
    ),
    path("party-ledgers/<str:kind>/", PartyLedgerSelectionView.as_view()),
    path("party-ledgers/<str:kind>/parties/", PartyLedgerLookupView.as_view()),
] + router.urls
