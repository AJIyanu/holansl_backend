from django.contrib import admin

from .models import (
    Address,
    AffiliationContactRole,
    ContactMethod,
    ContactRole,
    OrganisationProfile,
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
    PersonProfile,
)


class PartyRoleInline(admin.TabularInline):
    model = PartyRole
    extra = 0
    fields = (
        "role",
        "is_active",
        "activated_at",
        "deactivated_at",
        "notes",
    )
    readonly_fields = (
        "activated_at",
        "deactivated_at",
    )
    show_change_link = True


class ContactMethodInline(admin.TabularInline):
    model = ContactMethod
    extra = 0
    fields = (
        "method_type",
        "value",
        "label",
        "is_primary",
        "is_verified",
        "is_active",
    )
    show_change_link = True


class AddressInline(admin.StackedInline):
    model = Address
    extra = 0
    fields = (
        "address_type",
        "label",
        "line_1",
        "line_2",
        "city",
        "state_region",
        "postal_code",
        "country_code",
        "location_notes",
        "is_primary",
        "is_active",
    )
    show_change_link = True


class PartySourceInline(admin.StackedInline):
    model = PartySource
    extra = 0
    fields = (
        "source_type",
        "platform_name",
        "seller_name",
        "external_id",
        "profile_url",
        "listing_url",
        "market_name",
        "location_details",
        "referrer_name",
        "notes",
        "discovered_at",
        "last_verified_at",
        "discovered_by",
        "is_primary",
        "is_active",
    )
    autocomplete_fields = ("discovered_by",)
    show_change_link = True


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "entity_kind",
        "role_summary",
        "status",
        "verification_level",
        "is_archived",
        "updated_at",
    )

    list_filter = (
        "entity_kind",
        "status",
        "verification_level",
        "is_archived",
        "roles__role",
    )

    search_fields = (
        "display_name",
        "normalized_name",
        "contact_methods__value",
        "sources__seller_name",
        "sources__external_id",
        "sources__market_name",
    )

    autocomplete_fields = (
        "merged_into",
        "created_by",
        "updated_by",
    )

    readonly_fields = (
        "id",
        "normalized_name",
        "created_at",
        "updated_at",
        "archived_at",
    )

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "id",
                    "display_name",
                    "normalized_name",
                    "entity_kind",
                    "verification_level",
                )
            },
        ),
        (
            "Lifecycle",
            {
                "fields": (
                    "status",
                    "is_archived",
                    "archived_at",
                    "merged_into",
                )
            },
        ),
        (
            "Ownership",
            {
                "fields": (
                    "created_by",
                    "updated_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    inlines = (
        PartyRoleInline,
        ContactMethodInline,
        AddressInline,
        PartySourceInline,
    )

    list_select_related = (
        "merged_into",
        "created_by",
        "updated_by",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("roles")

    @admin.display(description="Roles")
    def role_summary(self, obj):
        return (
            ", ".join(
                role.get_role_display() for role in obj.roles.all() if role.is_active
            )
            or "—"
        )


@admin.register(PartyRole)
class PartyRoleAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "role",
        "is_active",
        "activated_at",
        "deactivated_at",
    )
    list_filter = (
        "role",
        "is_active",
    )
    search_fields = (
        "party__display_name",
        "notes",
    )
    autocomplete_fields = ("party",)
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(OrganisationProfile)
class OrganisationProfileAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "legal_name",
        "trading_name",
        "industry",
        "registration_country",
    )
    search_fields = (
        "party__display_name",
        "legal_name",
        "trading_name",
        "industry",
    )
    autocomplete_fields = ("party",)
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(PersonProfile)
class PersonProfileAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "first_name",
        "last_name",
        "preferred_name",
    )
    search_fields = (
        "party__display_name",
        "first_name",
        "middle_name",
        "last_name",
        "preferred_name",
    )
    autocomplete_fields = ("party",)
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(ContactRole)
class ContactRoleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "sort_order",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = (
        "name",
        "slug",
        "description",
    )
    prepopulated_fields = {"slug": ("name",)}
    ordering = (
        "sort_order",
        "name",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


class AffiliationContactRoleInline(admin.TabularInline):
    model = AffiliationContactRole
    extra = 0
    autocomplete_fields = ("contact_role",)


@admin.register(PartyAffiliation)
class PartyAffiliationAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "organisation",
        "job_title",
        "department",
        "is_current",
        "is_primary_contact",
    )
    list_filter = (
        "is_current",
        "is_primary_contact",
        "contact_roles",
    )
    search_fields = (
        "person__display_name",
        "organisation__display_name",
        "job_title",
        "department",
    )
    autocomplete_fields = (
        "person",
        "organisation",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    inlines = (AffiliationContactRoleInline,)


@admin.register(ContactMethod)
class ContactMethodAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "method_type",
        "value",
        "label",
        "is_primary",
        "is_verified",
        "is_active",
    )
    list_filter = (
        "method_type",
        "is_primary",
        "is_verified",
        "is_active",
    )
    search_fields = (
        "party__display_name",
        "value",
        "normalized_value",
        "label",
    )
    autocomplete_fields = ("party",)
    readonly_fields = (
        "normalized_value",
        "created_at",
        "updated_at",
    )


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "address_type",
        "label",
        "city",
        "country_code",
        "is_primary",
        "is_active",
    )
    list_filter = (
        "address_type",
        "country_code",
        "is_primary",
        "is_active",
    )
    search_fields = (
        "party__display_name",
        "label",
        "line_1",
        "line_2",
        "city",
        "state_region",
        "postal_code",
        "location_notes",
    )
    autocomplete_fields = ("party",)
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(PartySource)
class PartySourceAdmin(admin.ModelAdmin):
    list_display = (
        "party",
        "source_type",
        "platform_name",
        "reference_label",
        "is_primary",
        "is_active",
        "discovered_at",
    )
    list_filter = (
        "source_type",
        "platform_name",
        "is_primary",
        "is_active",
    )
    search_fields = (
        "party__display_name",
        "platform_name",
        "seller_name",
        "external_id",
        "market_name",
        "location_details",
        "referrer_name",
    )
    autocomplete_fields = (
        "party",
        "discovered_by",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(PartyNote)
class PartyNoteAdmin(admin.ModelAdmin):
    """
    Provide administrative management for CRM party notes.

    Accepts:
        Django admin requests and PartyNote records.

    Returns:
        Configured Django admin list and edit interfaces.
    """

    list_display = (
        "party",
        "note_type",
        "is_confidential",
        "author",
        "created_at",
    )

    list_filter = (
        "note_type",
        "is_confidential",
        "created_at",
    )

    search_fields = (
        "party__display_name",
        "content",
        "author__username",
        "author__email",
    )

    autocomplete_fields = (
        "party",
        "author",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(PartyInteraction)
class PartyInteractionAdmin(admin.ModelAdmin):
    """
    Provide administrative management for CRM interactions.

    Accepts:
        Django admin requests and PartyInteraction records.

    Returns:
        Configured Django admin list and edit interfaces.
    """

    list_display = (
        "party",
        "interaction_type",
        "contact_party",
        "staff_member",
        "occurred_at",
        "follow_up_at",
    )

    list_filter = (
        "interaction_type",
        "occurred_at",
        "follow_up_at",
    )

    search_fields = (
        "party__display_name",
        "contact_party__display_name",
        "subject",
        "summary",
    )

    autocomplete_fields = (
        "party",
        "contact_party",
        "staff_member",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(PartyStatusHistory)
class PartyStatusHistoryAdmin(admin.ModelAdmin):
    """
    Provide read-only administrative access to party status history.

    Accepts:
        Django admin requests and PartyStatusHistory records.

    Returns:
        A read-only Django admin history interface.
    """

    list_display = (
        "party",
        "previous_status",
        "new_status",
        "changed_by",
        "created_at",
    )

    list_filter = (
        "previous_status",
        "new_status",
        "created_at",
    )

    search_fields = (
        "party__display_name",
        "reason",
        "changed_by__username",
    )

    autocomplete_fields = (
        "party",
        "changed_by",
    )

    readonly_fields = (
        "party",
        "previous_status",
        "new_status",
        "reason",
        "changed_by",
        "metadata",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        """
        Prevent manual creation of status-history records.

        Accepts:
            The Django admin request.

        Returns:
            False.
        """

        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        """
        Prevent modification of status-history records.

        Accepts:
            The Django admin request and optional history record.

        Returns:
            False.
        """

        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        """
        Prevent deletion of status-history records.

        Accepts:
            The Django admin request and optional history record.

        Returns:
            False.
        """

        return False


@admin.register(PartyMergeRecord)
class PartyMergeRecordAdmin(admin.ModelAdmin):
    """
    Provide read-only administrative access to CRM merge records.

    Accepts:
        Django admin requests and PartyMergeRecord records.

    Returns:
        A read-only Django admin merge-history interface.
    """

    list_display = (
        "source_party",
        "target_party",
        "merged_by",
        "created_at",
    )

    search_fields = (
        "source_party__display_name",
        "target_party__display_name",
        "reason",
    )

    autocomplete_fields = (
        "source_party",
        "target_party",
        "merged_by",
    )

    readonly_fields = (
        "source_party",
        "target_party",
        "reason",
        "merged_by",
        "summary",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        """
        Prevent manual creation of merge-history records.

        Accepts:
            The Django admin request.

        Returns:
            False.
        """

        return False

    def has_change_permission(
        self,
        request,
        obj=None,
    ):
        """
        Prevent modification of merge-history records.

        Accepts:
            The Django admin request and optional merge record.

        Returns:
            False.
        """

        return False

    def has_delete_permission(
        self,
        request,
        obj=None,
    ):
        """
        Prevent deletion of merge-history records.

        Accepts:
            The Django admin request and optional merge record.

        Returns:
            False.
        """

        return False


@admin.register(PartyIdentifier)
class PartyIdentifierAdmin(admin.ModelAdmin):
    """Provide masked administrative visibility of CRM identifiers."""

    list_display = (
        "party",
        "identifier_type",
        "label",
        "masked_value",
        "issuing_country",
        "is_verified",
        "is_active",
    )

    list_filter = (
        "identifier_type",
        "issuing_country",
        "is_verified",
        "is_active",
    )

    search_fields = (
        "party__display_name",
        "label",
    )

    autocomplete_fields = (
        "party",
        "created_by",
        "updated_by",
    )

    readonly_fields = (
        "encrypted_value",
        "value_hash",
        "value_last_four",
        "created_at",
        "updated_at",
    )

    def has_add_permission(
        self,
        request,
    ) -> bool:
        """
        Prevent creation without the API encryption workflow.

        Args:
            request: Django admin request.

        Returns:
            bool: False.
        """

        return False


@admin.register(PartyBankAccount)
class PartyBankAccountAdmin(admin.ModelAdmin):
    """Provide masked administrative visibility of payment details."""

    list_display = (
        "party",
        "payment_method",
        "account_name",
        "bank_name",
        "masked_account_number",
        "currency",
        "verification_status",
        "is_primary",
        "is_active",
    )

    list_filter = (
        "payment_method",
        "currency",
        "verification_status",
        "is_primary",
        "is_active",
    )

    search_fields = (
        "party__display_name",
        "account_name",
        "bank_name",
        "provider_name",
    )

    autocomplete_fields = (
        "party",
        "created_by",
        "updated_by",
    )

    readonly_fields = (
        "encrypted_account_number",
        "account_number_hash",
        "account_number_last_four",
        "encrypted_iban",
        "iban_hash",
        "iban_last_four",
        "created_at",
        "updated_at",
    )

    def has_add_permission(
        self,
        request,
    ) -> bool:
        """
        Prevent creation without the API encryption workflow.

        Args:
            request: Django admin request.

        Returns:
            bool: False.
        """

        return False


@admin.register(PartyDocument)
class PartyDocumentAdmin(admin.ModelAdmin):
    """Provide administrative visibility of CRM document metadata."""

    list_display = (
        "original_filename",
        "party",
        "category",
        "storage_provider",
        "is_confidential",
        "verification_status",
        "expires_at",
        "is_active",
        "created_at",
    )

    list_filter = (
        "category",
        "storage_provider",
        "is_confidential",
        "verification_status",
        "is_active",
        "expires_at",
    )

    search_fields = (
        "party__display_name",
        "original_filename",
        "description",
        "checksum_sha256",
    )

    autocomplete_fields = (
        "party",
        "uploaded_by",
        "deleted_by",
    )

    readonly_fields = (
        "original_filename",
        "mime_type",
        "size_bytes",
        "checksum_sha256",
        "storage_provider",
        "external_file_id",
        "external_folder_id",
        "storage_path",
        "expiry_notification_key",
        "uploaded_by",
        "deleted_by",
        "deleted_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(
        self,
        request,
    ) -> bool:
        """
        Prevent admin uploads that bypass the storage service.

        Args:
            request: Django admin request.

        Returns:
            bool: False.
        """

        return False
