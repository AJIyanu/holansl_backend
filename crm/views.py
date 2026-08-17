"""
CRM API views.

This module exposes permission-controlled endpoints for CRM parties, roles,
contacts, affiliations, sources, notes, interactions and immutable history.

View classes accept authenticated HTTP requests and return paginated or
structured Django REST Framework responses.
"""

from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import (
    FormParser,
    JSONParser,
    MultiPartParser,
)
from rest_framework.response import Response

from accounts.models import AuditLog

from .document_services import (
    delete_party_document,
    download_party_document,
    upload_party_document,
)
from .filters import (
    PartyFilter,
    PartyInteractionFilter,
    PartyNoteFilter,
)
from .models import (
    Address,
    ContactMethod,
    ContactRole,
    Party,
    PartyAffiliation,
    # existing models...
    PartyBankAccount,
    PartyDocument,
    PartyIdentifier,
    PartyInteraction,
    PartyMergeRecord,
    PartyNote,
    PartyRole,
    PartySource,
    PartyStatusHistory,
)
from .notifications import (
    dispatch_bank_account_change,
    schedule_document_expiry_notification,
)
from .permissions import (
    PartyActionPermission,
    # existing permissions...
    PartyDocumentPermission,
    SensitiveBankAccountPermission,
    SensitiveIdentifierPermission,
    StrictDjangoModelPermissions,
)
from .serializers import (
    AddressSerializer,
    ContactMethodSerializer,
    ContactRoleSerializer,
    DuplicateCheckSerializer,
    OrganisationProfileInputSerializer,
    OrganisationProfileSerializer,
    PartyAffiliationSerializer,
    # existing serializers...
    PartyBankAccountSerializer,
    PartyDetailSerializer,
    PartyDocumentSerializer,
    PartyDocumentUpdateSerializer,
    PartyDocumentUploadSerializer,
    PartyIdentifierSerializer,
    PartyInteractionSerializer,
    PartyLifecycleSerializer,
    PartyListSerializer,
    PartyMergeRecordSerializer,
    PartyMergeSerializer,
    PartyNoteSerializer,
    PartyRoleSerializer,
    PartySourceSerializer,
    PartyStatusHistorySerializer,
    PartyWriteSerializer,
    PersonProfileInputSerializer,
    PersonProfileSerializer,
    QuickSupplierCreateSerializer,
)
from .services import (
    change_party_status,
    delete_unused_party,
    log_crm_event,
    merge_parties,
    set_party_archive_state,
)


class AuditedModelViewSetMixin:
    """
    Add central audit logging to ordinary CRM model mutations.

    Accepts:
        DRF serializers and model instances handled by a ModelViewSet.

    Returns:
        No direct value for perform hooks; writes central audit records.
    """

    def audit_instance(
        self,
        instance,
        *,
        action_name,
        event_type,
        metadata=None,
    ):
        """
        Write one audit entry for a CRM model instance.

        Accepts:
            Model instance, action name, audit event type and metadata.

        Returns:
            The created central AuditLog instance.
        """

        return log_crm_event(
            user=self.request.user,
            event_type=event_type,
            resource=instance._meta.model_name,
            action=action_name,
            object_id=instance.pk,
            request=self.request,
            metadata=metadata or {},
        )

    def perform_create(self, serializer):
        """
        Save a new CRM record and log the creation.

        Accepts:
            A validated DRF model serializer.

        Returns:
            None.
        """

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
        )

    def perform_update(self, serializer):
        """
        Save CRM record changes and log the update.

        Accepts:
            A validated DRF model serializer.

        Returns:
            None.
        """

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="update",
            event_type=AuditLog.EventType.UPDATE,
        )

    def perform_destroy(self, instance):
        """
        Delete an ordinary CRM child record and log the deletion.

        Accepts:
            The model instance selected for deletion.

        Returns:
            None.
        """

        object_id = instance.pk
        resource = instance._meta.model_name

        instance.delete()

        log_crm_event(
            user=self.request.user,
            event_type=AuditLog.EventType.DELETE,
            resource=resource,
            action="delete",
            object_id=object_id,
            request=self.request,
        )


class PartyViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage CRM party directory, lifecycle, duplicates and merges.

    Accepts:
        Authenticated requests containing party or lifecycle data.

    Returns:
        Paginated party lists, complete party details or action responses.
    """

    permission_classes = [
        PartyActionPermission,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = PartyFilter

    search_fields = [
        "display_name",
        "normalized_name",
        "organisation_profile__legal_name",
        "organisation_profile__trading_name",
        "person_profile__first_name",
        "person_profile__last_name",
        "contact_methods__value",
        "sources__platform_name",
        "sources__seller_name",
        "sources__external_id",
        "sources__market_name",
        "sources__referrer_name",
    ]

    ordering_fields = [
        "display_name",
        "created_at",
        "updated_at",
        "status",
        "verification_level",
    ]

    ordering = [
        "display_name",
        "id",
    ]

    def get_queryset(self):
        """
        Build an action-appropriate and query-efficient Party queryset.

        Accepts:
            The current request and resolved view action.

        Returns:
            A Party queryset with required related records prefetched.
        """

        active_roles = PartyRole.objects.order_by(
            "role",
        )

        active_contacts = ContactMethod.objects.order_by(
            "-is_primary",
            "method_type",
            "value",
        )

        active_sources = PartySource.objects.order_by(
            "-is_primary",
            "-discovered_at",
        )

        queryset = Party.objects.select_related(
            "merged_into",
            "created_by",
            "updated_by",
        ).prefetch_related(
            Prefetch(
                "roles",
                queryset=active_roles,
            ),
            Prefetch(
                "contact_methods",
                queryset=active_contacts,
            ),
            Prefetch(
                "sources",
                queryset=active_sources,
            ),
        )

        if self.action in {
            "retrieve",
            "update",
            "partial_update",
            "merge",
            "history",
        }:
            queryset = queryset.select_related(
                "organisation_profile",
                "person_profile",
            ).prefetch_related(
                "addresses",
                "people_affiliations__contact_roles",
                "organisation_affiliations__contact_roles",
            )

        if self.action == "list":
            query_parameters = self.request.query_params

            if (
                "is_archived" not in query_parameters
                and "status" not in query_parameters
            ):
                queryset = queryset.filter(
                    is_archived=False,
                ).exclude(
                    status=Party.Status.MERGED,
                )

        return queryset

    def filter_queryset(self, queryset):
        """
        Apply configured filters and remove duplicates caused by joins.

        Accepts:
            The base Party queryset.

        Returns:
            A filtered and distinct Party queryset.
        """

        return (
            super()
            .filter_queryset(
                queryset,
            )
            .distinct()
        )

    def get_serializer_class(self):
        """
        Select the serializer appropriate for the current Party action.

        Accepts:
            The current resolved view action.

        Returns:
            A DRF serializer class.
        """

        if self.action == "list":
            return PartyListSerializer

        if self.action == "retrieve":
            return PartyDetailSerializer

        if self.action in {
            "create",
            "update",
            "partial_update",
        }:
            return PartyWriteSerializer

        if self.action == "quick_create":
            return QuickSupplierCreateSerializer

        if self.action == "duplicate_check":
            return DuplicateCheckSerializer

        if self.action in {
            "deactivate",
            "reactivate",
            "suspend",
            "block",
            "archive",
            "restore",
        }:
            return PartyLifecycleSerializer

        if self.action == "merge":
            return PartyMergeSerializer

        return PartyDetailSerializer

    def create(self, request, *args, **kwargs):
        """
        Create a party and return the detailed party representation.

        Accepts:
            An authenticated POST request containing PartyWriteSerializer data.

        Returns:
            HTTP 201 with the newly created detailed party.
        """

        serializer = self.get_serializer(
            data=request.data,
        )
        serializer.is_valid(
            raise_exception=True,
        )

        self.perform_create(serializer)

        headers = self.get_success_headers(
            serializer.data,
        )

        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def update(self, request, *args, **kwargs):
        """
        Update a party and return the complete updated representation.

        Accepts:
            An authenticated PUT or PATCH request and party identifier.

        Returns:
            HTTP 200 with the updated detailed party.
        """

        partial = kwargs.pop(
            "partial",
            False,
        )

        instance = self.get_object()

        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        self.perform_update(serializer)

        return Response(
            serializer.data,
        )

    def destroy(self, request, *args, **kwargs):
        """
        Permanently delete an unused party through the safe deletion service.

        Accepts:
            An authenticated superuser DELETE request and party identifier.

        Returns:
            HTTP 204 when the unused party is deleted.
        """

        party = self.get_object()

        delete_unused_party(
            party_id=party.id,
            user=request.user,
            request=request,
        )

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    @action(
        detail=False,
        methods=[
            "post",
        ],
        url_path="quick-create",
    )
    def quick_create(self, request):
        """
        Quickly create a traceable minimal supplier.

        Accepts:
            An authenticated POST request with supplier contact or source data.

        Returns:
            HTTP 201 with the detailed supplier record.
        """

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        party = serializer.save()

        log_crm_event(
            user=request.user,
            event_type=AuditLog.EventType.CREATE,
            resource="party",
            action="quick_supplier_create",
            object_id=party.id,
            request=request,
        )

        return Response(
            PartyDetailSerializer(
                party,
                context={
                    "request": request,
                },
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=[
            "post",
        ],
        url_path="duplicate-check",
    )
    def duplicate_check(self, request):
        """
        Search for exact, strong and weak possible party duplicates.

        Accepts:
            An authenticated POST request containing duplicate identifiers.

        Returns:
            HTTP 200 with candidate count and matching records.
        """

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        matches = serializer.get_matches()

        return Response(
            {
                "count": len(matches),
                "results": matches,
            }
        )

    def _run_status_action(
        self,
        request,
        *,
        new_status,
    ):
        """
        Execute a validated status transition for the current party.

        Accepts:
            Request and target Party status value.

        Returns:
            HTTP 200 with the updated detailed party.
        """

        party = self.get_object()

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        party = change_party_status(
            party_id=party.id,
            new_status=new_status,
            reason=serializer.validated_data["reason"],
            user=request.user,
            request=request,
        )

        return Response(
            PartyDetailSerializer(
                party,
                context={
                    "request": request,
                },
            ).data
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def deactivate(self, request, pk=None):
        """
        Deactivate a party while preserving all historical references.

        Accepts:
            Party identifier and lifecycle reason.

        Returns:
            HTTP 200 with the inactive party.
        """

        return self._run_status_action(
            request,
            new_status=Party.Status.INACTIVE,
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def reactivate(self, request, pk=None):
        """
        Return an inactive, suspended or blocked party to active status.

        Accepts:
            Party identifier and lifecycle reason.

        Returns:
            HTTP 200 with the active party.
        """

        return self._run_status_action(
            request,
            new_status=Party.Status.ACTIVE,
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def suspend(self, request, pk=None):
        """
        Suspend a party from ordinary new business activity.

        Accepts:
            Party identifier and suspension reason.

        Returns:
            HTTP 200 with the suspended party.
        """

        return self._run_status_action(
            request,
            new_status=Party.Status.SUSPENDED,
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def block(self, request, pk=None):
        """
        Block a party from being selected for new transactions.

        Accepts:
            Party identifier and blocking reason.

        Returns:
            HTTP 200 with the blocked party.
        """

        return self._run_status_action(
            request,
            new_status=Party.Status.BLOCKED,
        )

    def _run_archive_action(
        self,
        request,
        *,
        archived,
    ):
        """
        Archive or restore the selected party.

        Accepts:
            Request and desired boolean archive state.

        Returns:
            HTTP 200 with the updated party.
        """

        party = self.get_object()

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        party = set_party_archive_state(
            party_id=party.id,
            archived=archived,
            reason=serializer.validated_data["reason"],
            user=request.user,
            request=request,
        )

        return Response(
            PartyDetailSerializer(
                party,
                context={
                    "request": request,
                },
            ).data
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def archive(self, request, pk=None):
        """
        Remove a party from the normal directory without deleting it.

        Accepts:
            Party identifier and archive reason.

        Returns:
            HTTP 200 with the archived party.
        """

        return self._run_archive_action(
            request,
            archived=True,
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def restore(self, request, pk=None):
        """
        Restore an archived party to the normal directory.

        Accepts:
            Party identifier and restoration reason.

        Returns:
            HTTP 200 with the restored party.
        """

        return self._run_archive_action(
            request,
            archived=False,
        )

    @action(
        detail=True,
        methods=[
            "post",
        ],
    )
    def merge(self, request, pk=None):
        """
        Merge the selected source party into a surviving target party.

        Accepts:
            Source party URL identifier, target party ID and merge reason.

        Returns:
            HTTP 200 with the surviving party and merge record.
        """

        source_party = self.get_object()

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        target_party, merge_record = merge_parties(
            source_party_id=source_party.id,
            target_party_id=(serializer.validated_data["target_party"].id),
            reason=serializer.validated_data["reason"],
            user=request.user,
            request=request,
        )

        return Response(
            {
                "party": PartyDetailSerializer(
                    target_party,
                    context={
                        "request": request,
                    },
                ).data,
                "merge": PartyMergeRecordSerializer(
                    merge_record,
                    context={
                        "request": request,
                    },
                ).data,
            }
        )

    @action(
        detail=True,
        methods=[
            "get",
        ],
    )
    def history(self, request, pk=None):
        """
        Return status and merge history associated with a party.

        Accepts:
            Party identifier.

        Returns:
            HTTP 200 containing status transitions and merge records.
        """

        party = self.get_object()

        status_records = party.status_history.select_related(
            "changed_by",
        ).all()

        merge_records = (
            PartyMergeRecord.objects.filter(
                Q(source_party=party) | Q(target_party=party)
            )
            .select_related(
                "source_party",
                "target_party",
                "merged_by",
            )
            .order_by("-created_at")
        )

        return Response(
            {
                "status_history": (
                    PartyStatusHistorySerializer(
                        status_records,
                        many=True,
                    ).data
                ),
                "merge_history": (
                    PartyMergeRecordSerializer(
                        merge_records,
                        many=True,
                    ).data
                ),
            }
        )

    @action(detail=True, methods=["patch"], url_path="profile")
    def profile(self, request, pk=None):
        """
        Update the party's person or organisation/trading-name profile.

        This keeps related profile updates outside normal Party PATCH,
        because PartyWriteSerializer intentionally blocks nested related
        updates after creation.
        """
        party = self.get_object()

        if party.entity_kind == Party.EntityKind.INDIVIDUAL:
            profile = party.person_profile
            serializer = PersonProfileInputSerializer(
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)

            for field_name, value in serializer.validated_data.items():
                setattr(profile, field_name, value)

            profile.save()

            return Response(
                PersonProfileSerializer(profile).data,
                status=status.HTTP_200_OK,
            )

        profile = party.organisation_profile
        serializer = OrganisationProfileInputSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        for field_name, value in serializer.validated_data.items():
            setattr(profile, field_name, value)

        profile.save()

        return Response(
            OrganisationProfileSerializer(profile).data,
            status=status.HTTP_200_OK,
        )


class PartyRoleViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage client, supplier and other classifications for CRM parties.

    Accepts:
        Authenticated CRUD requests containing PartyRole data.

    Returns:
        Paginated role records or mutation responses.
    """

    queryset = PartyRole.objects.select_related(
        "party",
    ).all()

    serializer_class = PartyRoleSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "role",
        "is_active",
    ]

    ordering_fields = [
        "role",
        "activated_at",
        "created_at",
    ]


class ContactMethodViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage email, phone, website and other party contact methods.

    Accepts:
        Authenticated CRUD requests containing ContactMethod data.

    Returns:
        Paginated contact records or mutation responses.
    """

    queryset = ContactMethod.objects.select_related(
        "party",
    ).all()

    serializer_class = ContactMethodSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "method_type",
        "is_primary",
        "is_verified",
        "is_active",
    ]

    search_fields = [
        "value",
        "label",
        "party__display_name",
    ]

    ordering_fields = [
        "method_type",
        "value",
        "created_at",
        "updated_at",
    ]


class AddressViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage formal addresses and informal market or trading locations.

    Accepts:
        Authenticated CRUD requests containing Address data.

    Returns:
        Paginated address records or mutation responses.
    """

    queryset = Address.objects.select_related(
        "party",
    ).all()

    serializer_class = AddressSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "address_type",
        "country_code",
        "is_primary",
        "is_active",
    ]

    search_fields = [
        "party__display_name",
        "label",
        "line_1",
        "line_2",
        "city",
        "state_region",
        "postal_code",
        "location_notes",
    ]

    ordering_fields = [
        "city",
        "country_code",
        "created_at",
        "updated_at",
    ]


class PartySourceViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage supplier provenance, marketplace and discovery information.

    Accepts:
        Authenticated CRUD requests containing PartySource data.

    Returns:
        Paginated source records or mutation responses.
    """

    queryset = PartySource.objects.select_related(
        "party",
        "discovered_by",
    ).all()

    serializer_class = PartySourceSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "source_type",
        "platform_name",
        "is_primary",
        "is_active",
    ]

    search_fields = [
        "party__display_name",
        "platform_name",
        "seller_name",
        "external_id",
        "market_name",
        "location_details",
        "referrer_name",
        "notes",
    ]

    ordering_fields = [
        "platform_name",
        "discovered_at",
        "created_at",
        "updated_at",
    ]

    def perform_create(self, serializer):
        """
        Save a source with the authenticated discoverer and audit the action.

        Accepts:
            A validated PartySourceSerializer.

        Returns:
            None.
        """

        instance = serializer.save(
            discovered_by=self.request.user,
        )

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
        )


class ContactRoleViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage configurable roles held by organisation contacts.

    Accepts:
        Authenticated CRUD requests containing ContactRole data.

    Returns:
        Paginated contact-role records or mutation responses.
    """

    queryset = ContactRole.objects.all()
    serializer_class = ContactRoleSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "is_active",
    ]

    search_fields = [
        "name",
        "slug",
        "description",
    ]

    ordering_fields = [
        "sort_order",
        "name",
        "created_at",
    ]


class PartyAffiliationViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage relationships between individuals and organisations.

    Accepts:
        Authenticated CRUD requests containing affiliation data.

    Returns:
        Paginated affiliation records or mutation responses.
    """

    queryset = (
        PartyAffiliation.objects.select_related(
            "person",
            "organisation",
        )
        .prefetch_related(
            "contact_roles",
        )
        .all()
    )

    serializer_class = PartyAffiliationSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "person",
        "organisation",
        "is_current",
        "is_primary_contact",
        "contact_roles",
    ]

    search_fields = [
        "person__display_name",
        "organisation__display_name",
        "job_title",
        "department",
        "notes",
    ]

    ordering_fields = [
        "start_date",
        "end_date",
        "created_at",
        "updated_at",
    ]


class PartyNoteViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage permission-filtered ordinary and confidential CRM notes.

    Accepts:
        Authenticated CRUD requests containing PartyNote data.

    Returns:
        Paginated visible notes or mutation responses.
    """

    serializer_class = PartyNoteSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = PartyNoteFilter

    search_fields = [
        "party__display_name",
        "content",
    ]

    ordering_fields = [
        "created_at",
        "updated_at",
        "note_type",
    ]

    ordering = [
        "-created_at",
    ]

    def get_queryset(self):
        """
        Return notes visible to the current user.

        Accepts:
            The authenticated request user.

        Returns:
            A PartyNote queryset excluding confidential notes when necessary.
        """

        queryset = PartyNote.objects.select_related(
            "party",
            "author",
        )

        if not self.request.user.has_perm("crm.view_confidentialnote"):
            queryset = queryset.filter(
                is_confidential=False,
            )

        return queryset

    def perform_create(self, serializer):
        """
        Save a note with its authenticated author and audit the creation.

        Accepts:
            A validated PartyNoteSerializer.

        Returns:
            None.
        """

        instance = serializer.save(
            author=self.request.user,
        )

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
        )


class PartyInteractionViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """
    Manage CRM communication and contact history.

    Accepts:
        Authenticated CRUD requests containing PartyInteraction data.

    Returns:
        Paginated interactions or mutation responses.
    """

    queryset = PartyInteraction.objects.select_related(
        "party",
        "contact_party",
        "staff_member",
    ).all()

    serializer_class = PartyInteractionSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = PartyInteractionFilter

    search_fields = [
        "party__display_name",
        "contact_party__display_name",
        "subject",
        "summary",
    ]

    ordering_fields = [
        "occurred_at",
        "follow_up_at",
        "created_at",
        "updated_at",
    ]

    ordering = [
        "-occurred_at",
    ]

    def perform_create(self, serializer):
        """
        Save an interaction with the authenticated recorder and audit it.

        Accepts:
            A validated PartyInteractionSerializer.

        Returns:
            None.
        """

        instance = serializer.save(
            staff_member=self.request.user,
        )

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
        )


class PartyStatusHistoryViewSet(
    viewsets.ReadOnlyModelViewSet,
):
    """
    Expose immutable CRM status history to authorised users.

    Accepts:
        Authenticated read requests and optional party filter.

    Returns:
        Paginated PartyStatusHistory records.
    """

    queryset = PartyStatusHistory.objects.select_related(
        "party",
        "changed_by",
    ).all()

    serializer_class = PartyStatusHistorySerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "previous_status",
        "new_status",
        "changed_by",
    ]

    ordering_fields = [
        "created_at",
        "new_status",
    ]


class PartyMergeRecordViewSet(
    viewsets.ReadOnlyModelViewSet,
):
    """
    Expose immutable CRM merge history to authorised users.

    Accepts:
        Authenticated read requests and optional source or target filter.

    Returns:
        Paginated PartyMergeRecord records.
    """

    queryset = PartyMergeRecord.objects.select_related(
        "source_party",
        "target_party",
        "merged_by",
    ).all()

    serializer_class = PartyMergeRecordSerializer
    permission_classes = [
        StrictDjangoModelPermissions,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "source_party",
        "target_party",
        "merged_by",
    ]

    ordering_fields = [
        "created_at",
    ]


class PartyIdentifierViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """Manage encrypted party identifiers and audited plaintext reveals."""

    queryset = PartyIdentifier.objects.select_related(
        "party",
        "created_by",
        "updated_by",
    ).all()

    serializer_class = PartyIdentifierSerializer
    permission_classes = [
        SensitiveIdentifierPermission,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "identifier_type",
        "issuing_country",
        "is_verified",
        "is_active",
    ]

    ordering_fields = [
        "identifier_type",
        "issue_date",
        "expiry_date",
        "created_at",
        "updated_at",
    ]

    def perform_create(self, serializer):
        """
        Save and audit an encrypted identifier.

        Args:
            serializer: Validated PartyIdentifierSerializer.

        Returns:
            None.
        """

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
            metadata={
                "party_id": str(
                    instance.party_id,
                ),
                "identifier_type": (instance.identifier_type),
                "masked_value": (instance.masked_value),
            },
        )

    @action(
        detail=True,
        methods=[
            "get",
        ],
    )
    def reveal(self, request, pk=None):
        """
        Reveal one identifier and record the sensitive read.

        Args:
            request: Authenticated DRF request.
            pk: PartyIdentifier UUID.

        Returns:
            Response: Plaintext identifier response.
        """

        identifier = self.get_object()

        value = identifier.reveal_value()

        log_crm_event(
            user=request.user,
            event_type=AuditLog.EventType.READ,
            resource="partyidentifier",
            action="reveal",
            object_id=identifier.id,
            request=request,
            metadata={
                "party_id": str(
                    identifier.party_id,
                ),
                "identifier_type": (identifier.identifier_type),
                "masked_value": (identifier.masked_value),
            },
        )

        return Response(
            {
                "id": str(identifier.id),
                "value": value,
            }
        )


class PartyBankAccountViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """Manage encrypted payment details and audited plaintext reveals."""

    queryset = PartyBankAccount.objects.select_related(
        "party",
        "created_by",
        "updated_by",
    ).all()

    serializer_class = PartyBankAccountSerializer
    permission_classes = [
        SensitiveBankAccountPermission,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "payment_method",
        "currency",
        "country_code",
        "verification_status",
        "is_primary",
        "is_active",
    ]

    ordering_fields = [
        "account_name",
        "bank_name",
        "created_at",
        "updated_at",
    ]

    def perform_create(self, serializer):
        """
        Save, audit and notify authorised staff of new payment details.

        Args:
            serializer: Validated PartyBankAccountSerializer.

        Returns:
            None.
        """

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="create",
            event_type=AuditLog.EventType.CREATE,
            metadata={
                "party_id": str(
                    instance.party_id,
                ),
                "masked_account_number": (instance.masked_account_number),
            },
        )

        transaction.on_commit(
            lambda: dispatch_bank_account_change(
                bank_account_id=instance.id,
                actor_id=self.request.user.id,
                action="created",
            )
        )

    def perform_update(self, serializer):
        """
        Save, audit and notify authorised staff of changed payment details.

        Args:
            serializer: Validated PartyBankAccountSerializer.

        Returns:
            None.
        """

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="update",
            event_type=AuditLog.EventType.UPDATE,
            metadata={
                "party_id": str(
                    instance.party_id,
                ),
                "masked_account_number": (instance.masked_account_number),
            },
        )

        transaction.on_commit(
            lambda: dispatch_bank_account_change(
                bank_account_id=instance.id,
                actor_id=self.request.user.id,
                action="updated",
            )
        )

    @action(
        detail=True,
        methods=[
            "get",
        ],
    )
    def reveal(self, request, pk=None):
        """
        Reveal account details and record the sensitive read.

        Args:
            request: Authenticated DRF request.
            pk: PartyBankAccount UUID.

        Returns:
            Response: Plaintext account-number and IBAN values.
        """

        account = self.get_object()

        response_data = {
            "id": str(account.id),
            "account_number": (account.reveal_account_number()),
            "iban": account.reveal_iban(),
        }

        log_crm_event(
            user=request.user,
            event_type=AuditLog.EventType.READ,
            resource="partybankaccount",
            action="reveal",
            object_id=account.id,
            request=request,
            metadata={
                "party_id": str(
                    account.party_id,
                ),
                "masked_account_number": (account.masked_account_number),
                "masked_iban": (account.masked_iban),
            },
        )

        return Response(response_data)


class PartyDocumentViewSet(
    AuditedModelViewSetMixin,
    viewsets.ModelViewSet,
):
    """Manage externally stored CRM document metadata and file operations."""

    permission_classes = [
        PartyDocumentPermission,
    ]

    parser_classes = [
        MultiPartParser,
        FormParser,
        JSONParser,
    ]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "party",
        "category",
        "storage_provider",
        "is_confidential",
        "verification_status",
        "is_active",
        "expires_at",
    ]

    search_fields = [
        "party__display_name",
        "original_filename",
        "description",
        "checksum_sha256",
    ]

    ordering_fields = [
        "original_filename",
        "category",
        "expires_at",
        "created_at",
        "updated_at",
    ]

    ordering = [
        "-created_at",
    ]

    def get_queryset(self):
        """
        Return documents visible to the authenticated user.

        Returns:
            QuerySet: Permission-filtered PartyDocument queryset.
        """

        queryset = PartyDocument.objects.select_related(
            "party",
            "uploaded_by",
            "deleted_by",
        )

        if not self.request.user.has_perm("crm.view_confidential_partydocument"):
            queryset = queryset.filter(
                is_confidential=False,
            )

        include_deleted = self.request.query_params.get(
            "include_deleted",
            "",
        ).lower() in {
            "1",
            "true",
            "yes",
        }

        if not include_deleted:
            queryset = queryset.filter(
                is_active=True,
            )

        return queryset

    def get_serializer_class(self):
        """
        Select a serializer for upload, metadata update or read operations.

        Returns:
            type[Serializer]: DRF serializer class.
        """

        if self.action == "create":
            return PartyDocumentUploadSerializer

        if self.action in {
            "update",
            "partial_update",
        }:
            return PartyDocumentUpdateSerializer

        return PartyDocumentSerializer

    def create(self, request, *args, **kwargs):
        """
        Upload a file and create its CRM metadata record.

        Args:
            request: Authenticated multipart DRF request.
            *args: Positional arguments passed by DRF.
            **kwargs: Keyword arguments passed by DRF.

        Returns:
            Response: HTTP 201 document metadata.
        """

        serializer = self.get_serializer(
            data=request.data,
        )

        serializer.is_valid(
            raise_exception=True,
        )

        data = serializer.validated_data

        document = upload_party_document(
            party=data["party"],
            uploaded_file=data["file"],
            category=data["category"],
            description=data.get(
                "description",
                "",
            ),
            is_confidential=data.get(
                "is_confidential",
                False,
            ),
            verification_status=data.get(
                "verification_status",
                (PartyDocument.VerificationStatus.UNVERIFIED),
            ),
            expires_at=data.get(
                "expires_at",
            ),
            user=request.user,
            request=request,
        )

        return Response(
            PartyDocumentSerializer(
                document,
                context={
                    "request": request,
                },
            ).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_update(self, serializer):
        """
        Save document metadata and reschedule expiry warning if required.

        Args:
            serializer: Validated PartyDocumentUpdateSerializer.

        Returns:
            None.
        """

        previous_expiry = serializer.instance.expires_at

        previous_confidential = serializer.instance.is_confidential

        instance = serializer.save()

        self.audit_instance(
            instance,
            action_name="update",
            event_type=AuditLog.EventType.UPDATE,
            metadata={
                "party_id": str(
                    instance.party_id,
                ),
                "expires_at": (
                    instance.expires_at.isoformat() if instance.expires_at else None
                ),
                "is_confidential": (instance.is_confidential),
            },
        )

        if (
            previous_expiry != instance.expires_at
            or previous_confidential != instance.is_confidential
        ):
            transaction.on_commit(
                lambda: (
                    schedule_document_expiry_notification(
                        document_id=instance.id,
                        actor_id=self.request.user.id,
                    )
                    if instance.expires_at
                    else None
                )
            )

    def destroy(self, request, *args, **kwargs):
        """
        Delete the remote file and retain inactive metadata.

        Args:
            request: Authenticated DRF request.
            *args: Positional arguments passed by DRF.
            **kwargs: Keyword arguments passed by DRF.

        Returns:
            Response: HTTP 204 on success.
        """

        document = self.get_object()

        delete_party_document(
            document=document,
            user=request.user,
            request=request,
        )

        return Response(
            status=status.HTTP_204_NO_CONTENT,
        )

    @action(
        detail=True,
        methods=[
            "get",
        ],
    )
    def download(self, request, pk=None):
        """
        Stream a stored CRM document to an authorised user.

        Args:
            request: Authenticated DRF request.
            pk: PartyDocument UUID.

        Returns:
            FileResponse: Attachment response backed by a temporary stream.
        """

        document = self.get_object()

        downloaded = download_party_document(
            document=document,
            user=request.user,
            request=request,
        )

        response = FileResponse(
            downloaded.file_object,
            as_attachment=True,
            filename=downloaded.filename,
            content_type=downloaded.mime_type,
        )

        if downloaded.size_bytes is not None:
            response["Content-Length"] = str(downloaded.size_bytes)

        return response
