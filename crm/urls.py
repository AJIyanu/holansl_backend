"""
CRM API URL configuration.

This module registers the CRM REST endpoints with Django REST Framework's
router.

It accepts incoming paths beneath ``/crm/`` and returns the matched viewset
response.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AddressViewSet,
    ContactMethodViewSet,
    ContactRoleViewSet,
    PartyAffiliationViewSet,
    # existing viewsets...
    PartyBankAccountViewSet,
    PartyDocumentViewSet,
    PartyIdentifierViewSet,
    PartyInteractionViewSet,
    PartyMergeRecordViewSet,
    PartyNoteViewSet,
    PartyRoleViewSet,
    PartySourceViewSet,
    PartyStatusHistoryViewSet,
    PartyViewSet,
)

router = DefaultRouter()

router.register(
    "parties",
    PartyViewSet,
    basename="party",
)

router.register(
    "party-roles",
    PartyRoleViewSet,
    basename="party-role",
)

router.register(
    "contact-methods",
    ContactMethodViewSet,
    basename="contact-method",
)

router.register(
    "addresses",
    AddressViewSet,
    basename="address",
)

router.register(
    "sources",
    PartySourceViewSet,
    basename="party-source",
)

router.register(
    "contact-roles",
    ContactRoleViewSet,
    basename="contact-role",
)

router.register(
    "affiliations",
    PartyAffiliationViewSet,
    basename="party-affiliation",
)

router.register(
    "notes",
    PartyNoteViewSet,
    basename="party-note",
)

router.register(
    "interactions",
    PartyInteractionViewSet,
    basename="party-interaction",
)

router.register(
    "status-history",
    PartyStatusHistoryViewSet,
    basename="party-status-history",
)

router.register(
    "merge-history",
    PartyMergeRecordViewSet,
    basename="party-merge-history",
)

router.register(
    "identifiers",
    PartyIdentifierViewSet,
    basename="party-identifier",
)

router.register(
    "bank-accounts",
    PartyBankAccountViewSet,
    basename="party-bank-account",
)

router.register(
    "documents",
    PartyDocumentViewSet,
    basename="party-document",
)


urlpatterns = [
    path(
        "",
        include(router.urls),
    ),
]
