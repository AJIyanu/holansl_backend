from django.urls import path
from rest_framework.routers import DefaultRouter
from .party_ledgers import (
    PartyLedgerSelectionView,
    PartyLedgerLookupView,
    PartyLedgerTransactionsView,
    PartyOutstandingDocumentsView,
)

from .workspace_api import (
    ProcurementInboxView,
    TransactionChoicesView,
    OperationsView,
    StatementsView,
    StatementView,
    AccountsView,
)

from .invoice_api import InvoiceListView, InvoiceView, InvoiceActionView, InvoicePDFView

from .receipt_api import ReceiptListView, ReceiptView, ReceiptPDFView, ReceiptEmailView

from .views import CashBookViewSet, CashBookJournalViewSet

router = DefaultRouter()
router.register("cash-book", CashBookViewSet, basename="cash-book")
router.register(
    "cash-book-journal", CashBookJournalViewSet, basename="cash-book-journal"
)
urlpatterns = [
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
    path("procurement-inbox/", ProcurementInboxView.as_view()),
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
