"""
CRM integration rules shared with Procurement, Ledger and other apps.

This module validates Party records selected for business operations and
updates registered external references when duplicate CRM parties are merged.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from .models import (
    Party,
    PartyRole,
)


def party_has_role(
    party: Party,
    role: str,
    *,
    active_only: bool = True,
) -> bool:
    """
    Check whether a party has a specified business role.

    Args:
        party: CRM Party instance to inspect.
        role: PartyRole value such as ``CLIENT`` or ``SUPPLIER``.
        active_only: Whether only active PartyRole records should count.

    Returns:
        bool: True when the party has the requested role.
    """

    queryset = party.roles.filter(
        role=role,
    )

    if active_only:
        queryset = queryset.filter(
            is_active=True,
        )

    return queryset.exists()


def validate_party_referenceable(
    party: Party | None,
    *,
    field_name: str = "party",
) -> None:
    """
    Validate that a party may be referenced by a new business record.

    Inactive or blocked parties may remain on historical records, but merged
    parties must not be selected for new records.

    Args:
        party: Party selected by the caller.
        field_name: Field name used in a ValidationError response.

    Returns:
        None.

    Raises:
        ValidationError: If the party is missing or has been merged.
    """

    if party is None:
        raise ValidationError(
            {
                field_name: "A CRM party is required.",
            }
        )

    if party.status == Party.Status.MERGED:
        raise ValidationError(
            {
                field_name: (
                    "This CRM party has been merged. Select the "
                    "surviving party instead."
                )
            }
        )


def validate_party_for_new_business(
    party: Party | None,
    *,
    role: str,
    field_name: str = "party",
) -> None:
    """
    Validate a party selected for a new client or supplier operation.

    Args:
        party: CRM Party selected for the operation.
        role: Required PartyRole value.
        field_name: Field name used in validation errors.

    Returns:
        None.

    Raises:
        ValidationError: If the party is unavailable, archived, inactive,
            suspended, blocked, merged, or lacks the required active role.
    """

    validate_party_referenceable(
        party,
        field_name=field_name,
    )

    if party.is_archived:
        raise ValidationError(
            {
                field_name: (
                    "Archived CRM parties cannot be used for new business records."
                )
            }
        )

    if party.status != Party.Status.ACTIVE:
        raise ValidationError(
            {
                field_name: (
                    "Only active CRM parties may be selected for new business records."
                )
            }
        )

    if not party_has_role(
        party,
        role,
        active_only=True,
    ):
        role_label = dict(
            PartyRole.Role.choices,
        ).get(
            role,
            role,
        )

        raise ValidationError(
            {
                field_name: (
                    f"The selected party does not have an active {role_label} role."
                )
            }
        )


def validate_client_contact(
    *,
    client: Party,
    contact_party: Party | None,
    field_name: str = "contact_party",
) -> None:
    """
    Validate a person selected as the contact for a client.

    An organisational client contact must have an affiliation with that
    organisation. An individual client may act as their own contact.

    Args:
        client: Client Party associated with the business record.
        contact_party: Optional individual Party selected as the contact.
        field_name: Field name used in validation errors.

    Returns:
        None.

    Raises:
        ValidationError: If the contact is not an individual, has been merged,
            or does not represent the selected organisational client.
    """

    if contact_party is None:
        return

    validate_party_referenceable(
        contact_party,
        field_name=field_name,
    )

    if contact_party.entity_kind != Party.EntityKind.INDIVIDUAL:
        raise ValidationError(
            {field_name: ("A client contact must be an individual CRM party.")}
        )

    if client.entity_kind == Party.EntityKind.INDIVIDUAL:
        if contact_party.id != client.id:
            raise ValidationError(
                {
                    field_name: (
                        "An individual client may only be selected as "
                        "their own direct contact."
                    )
                }
            )

        return

    affiliation_exists = contact_party.organisation_affiliations.filter(
        organisation=client,
        is_current=True,
    ).exists()

    if not affiliation_exists:
        raise ValidationError(
            {
                field_name: (
                    "The selected contact does not have a current "
                    "affiliation with this client."
                )
            }
        )
