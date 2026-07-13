"""
CRM API filters.

This module defines server-side filters for the CRM party directory and
related activity endpoints.

Filter methods accept a queryset, filter name and submitted value and return
a filtered queryset.
"""

import django_filters
from django.db.models import Q

from .models import (
    Party,
    PartyInteraction,
    PartyNote,
    PartyRole,
    PartySource,
)


class PartyFilter(django_filters.FilterSet):
    """
    Filter CRM parties by identity, role, status, source and contact details.

    Accepts:
        Query parameters supplied to the CRM party directory endpoint.

    Returns:
        A filtered Party queryset.
    """

    role = django_filters.MultipleChoiceFilter(
        choices=PartyRole.Role.choices,
        method="filter_role",
    )

    source_type = django_filters.MultipleChoiceFilter(
        choices=PartySource.SourceType.choices,
        method="filter_source_type",
    )

    platform = django_filters.CharFilter(
        method="filter_platform",
    )

    contact_role = django_filters.UUIDFilter(
        method="filter_contact_role",
    )

    organisation = django_filters.UUIDFilter(
        method="filter_organisation",
    )

    has_email = django_filters.BooleanFilter(
        method="filter_has_email",
    )

    has_phone = django_filters.BooleanFilter(
        method="filter_has_phone",
    )

    has_source = django_filters.BooleanFilter(
        method="filter_has_source",
    )

    created_after = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )

    created_before = django_filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    updated_after = django_filters.IsoDateTimeFilter(
        field_name="updated_at",
        lookup_expr="gte",
    )

    updated_before = django_filters.IsoDateTimeFilter(
        field_name="updated_at",
        lookup_expr="lte",
    )

    class Meta:
        """Configure directly mapped Party model filter fields."""

        model = Party

        fields = {
            "entity_kind": [
                "exact",
            ],
            "status": [
                "exact",
            ],
            "verification_level": [
                "exact",
            ],
            "is_archived": [
                "exact",
            ],
        }

    def filter_role(self, queryset, name, value):
        """
        Filter parties holding any selected active CRM role.

        Accepts:
            A Party queryset, filter name and collection of role values.

        Returns:
            A distinct Party queryset containing matching active roles.
        """

        if not value:
            return queryset

        return queryset.filter(
            roles__role__in=value,
            roles__is_active=True,
        ).distinct()

    def filter_source_type(self, queryset, name, value):
        """
        Filter parties by active supplier or discovery source types.

        Accepts:
            A Party queryset, filter name and collection of source types.

        Returns:
            A distinct Party queryset containing matching active sources.
        """

        if not value:
            return queryset

        return queryset.filter(
            sources__source_type__in=value,
            sources__is_active=True,
        ).distinct()

    def filter_platform(self, queryset, name, value):
        """
        Filter parties by marketplace or source platform name.

        Accepts:
            A Party queryset, filter name and platform search string.

        Returns:
            A distinct Party queryset matching the platform name.
        """

        if not value:
            return queryset

        return queryset.filter(
            sources__platform_name__icontains=value,
            sources__is_active=True,
        ).distinct()

    def filter_contact_role(self, queryset, name, value):
        """
        Filter people or organisations by an affiliation contact role.

        Accepts:
            A Party queryset, filter name and ContactRole UUID.

        Returns:
            A distinct Party queryset connected to the contact role.
        """

        if not value:
            return queryset

        return queryset.filter(
            Q(
                organisation_affiliations__contact_roles__id=value,
            )
            | Q(
                people_affiliations__contact_roles__id=value,
            )
        ).distinct()

    def filter_organisation(self, queryset, name, value):
        """
        Filter individual parties currently affiliated with an organisation.

        Accepts:
            A Party queryset, filter name and organisation party UUID.

        Returns:
            A distinct queryset of people connected to the organisation.
        """

        if not value:
            return queryset

        return queryset.filter(
            organisation_affiliations__organisation_id=value,
            organisation_affiliations__is_current=True,
        ).distinct()

    def filter_has_email(self, queryset, name, value):
        """
        Filter parties based on whether they have an active email address.

        Accepts:
            A Party queryset, filter name and boolean value.

        Returns:
            A distinct queryset satisfying the requested email condition.
        """

        lookup = Q(
            contact_methods__method_type="EMAIL",
            contact_methods__is_active=True,
        )

        if value:
            return queryset.filter(lookup).distinct()

        return queryset.exclude(lookup).distinct()

    def filter_has_phone(self, queryset, name, value):
        """
        Filter parties based on active phone or messaging contact details.

        Accepts:
            A Party queryset, filter name and boolean value.

        Returns:
            A distinct queryset satisfying the requested phone condition.
        """

        lookup = Q(
            contact_methods__method_type__in=[
                "PHONE",
                "MOBILE",
                "WHATSAPP",
            ],
            contact_methods__is_active=True,
        )

        if value:
            return queryset.filter(lookup).distinct()

        return queryset.exclude(lookup).distinct()

    def filter_has_source(self, queryset, name, value):
        """
        Filter parties based on whether an active source record exists.

        Accepts:
            A Party queryset, filter name and boolean value.

        Returns:
            A distinct queryset satisfying the requested source condition.
        """

        if value:
            return queryset.filter(
                sources__is_active=True,
            ).distinct()

        return queryset.exclude(
            sources__is_active=True,
        ).distinct()


class PartyNoteFilter(django_filters.FilterSet):
    """
    Filter CRM notes by party, type, author and confidentiality.

    Accepts:
        Query parameters supplied to the notes endpoint.

    Returns:
        A filtered PartyNote queryset.
    """

    class Meta:
        """Configure PartyNote filterable fields."""

        model = PartyNote
        fields = [
            "party",
            "note_type",
            "author",
            "is_confidential",
        ]


class PartyInteractionFilter(django_filters.FilterSet):
    """
    Filter CRM interactions by party, contact, type and date.

    Accepts:
        Query parameters supplied to the interactions endpoint.

    Returns:
        A filtered PartyInteraction queryset.
    """

    occurred_after = django_filters.IsoDateTimeFilter(
        field_name="occurred_at",
        lookup_expr="gte",
    )

    occurred_before = django_filters.IsoDateTimeFilter(
        field_name="occurred_at",
        lookup_expr="lte",
    )

    class Meta:
        """Configure PartyInteraction filterable fields."""

        model = PartyInteraction
        fields = [
            "party",
            "contact_party",
            "interaction_type",
            "staff_member",
        ]
