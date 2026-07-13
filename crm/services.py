"""
CRM business services.

This module contains transactional operations that should not be implemented
directly inside API views or serializers, including auditing, status changes,
duplicate detection, safe deletion and party merging.

Public service functions accept model identifiers or validated values and
return updated model instances, structured result dictionaries or deletion
counts.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)

from accounts.models import AuditLog
from accounts.utils import create_audit_log

from .exceptions import CRMConflict
from .models import (
    AffiliationContactRole,
    Party,
    PartyAffiliation,
    PartyInteraction,
    PartyMergeRecord,
    PartyNote,
    PartyRole,
    PartyStatusHistory,
)
from .normalizers import (
    normalize_contact_value,
    normalize_party_name,
    normalize_text,
    normalize_url,
)

SENSITIVE_AUDIT_KEYS = {
    "account_number",
    "bank_account",
    "bank_details",
    "password",
    "secret",
    "tax_number",
    "token",
}


def sanitize_audit_metadata(value: Any) -> Any:
    """
    Remove sensitive values from nested audit metadata.

    Accepts:
        A dictionary, list, tuple or scalar value intended for an audit record.

    Returns:
        A recursively sanitised value safe for ordinary audit storage.
    """

    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            normalised_key = str(key).casefold()

            if any(
                sensitive_key in normalised_key
                for sensitive_key in SENSITIVE_AUDIT_KEYS
            ):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = sanitize_audit_metadata(item)

        return cleaned

    if isinstance(value, (list, tuple)):
        return [sanitize_audit_metadata(item) for item in value]

    return value


def log_crm_event(
    *,
    user,
    event_type: str,
    resource: str,
    action: str,
    object_id: str | UUID,
    request=None,
    metadata: dict[str, Any] | None = None,
    status: str = AuditLog.EventStatus.SUCCESS,
):
    """
    Write a CRM event to the existing central account audit log.

    Accepts:
        The acting user, audit event type, resource, action, object ID,
        optional request, optional metadata and optional event status.

    Returns:
        The created accounts.AuditLog instance.
    """

    return create_audit_log(
        user=(user if user and user.is_authenticated else None),
        event_category=AuditLog.EventCategory.CRUD,
        event_type=event_type,
        status=status,
        app_label="crm",
        resource=resource,
        action=action,
        object_id=object_id,
        request=request,
        metadata=sanitize_audit_metadata(
            metadata or {},
        ),
    )


def build_duplicate_probe_from_party(
    party: Party,
) -> dict[str, Any]:
    """
    Build duplicate-search input from an existing CRM party.

    Accepts:
        A Party instance with contact methods and sources available.

    Returns:
        A dictionary accepted by ``find_party_duplicates``.
    """

    return {
        "display_name": party.display_name,
        "entity_kind": party.entity_kind,
        "contact_methods": [
            {
                "method_type": item.method_type,
                "value": item.value,
            }
            for item in party.contact_methods.filter(
                is_active=True,
            )
        ],
        "sources": [
            {
                "platform_name": item.platform_name,
                "seller_name": item.seller_name,
                "external_id": item.external_id,
                "profile_url": item.profile_url,
                "listing_url": item.listing_url,
            }
            for item in party.sources.filter(
                is_active=True,
            )
        ],
    }


def find_party_duplicates(
    probe: dict[str, Any],
    *,
    exclude_party_id: UUID | str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Find exact, strong and weak possible CRM party duplicates.

    Accepts:
        A duplicate probe dictionary, optional party ID to exclude and
        maximum number of database candidates to inspect.

    Returns:
        A sorted list of candidate dictionaries containing classification,
        score, signals, party ID, name, kind and status.
    """

    display_name = normalize_party_name(
        probe.get("display_name"),
    )

    contact_methods = probe.get(
        "contact_methods",
        [],
    )

    sources = probe.get(
        "sources",
        [],
    )

    probe_emails = {
        normalize_contact_value(
            "EMAIL",
            item.get("value"),
        )
        for item in contact_methods
        if item.get("method_type") == "EMAIL" and item.get("value")
    }

    probe_phones = {
        normalize_contact_value(
            item.get("method_type", ""),
            item.get("value"),
        )
        for item in contact_methods
        if item.get("method_type")
        in {
            "PHONE",
            "MOBILE",
            "WHATSAPP",
        }
        and item.get("value")
    }

    probe_external_sources = {
        (
            normalize_text(
                item.get("platform_name"),
            ).casefold(),
            normalize_text(
                item.get("external_id"),
            ).casefold(),
        )
        for item in sources
        if item.get("platform_name") and item.get("external_id")
    }

    probe_profile_urls = {
        normalize_url(item.get("profile_url"))
        for item in sources
        if item.get("profile_url")
    }

    probe_listing_urls = {
        normalize_url(item.get("listing_url"))
        for item in sources
        if item.get("listing_url")
    }

    search_conditions = []

    if display_name:
        search_conditions.append(Q(normalized_name=display_name))

        first_name_token = display_name.split(" ")[0]

        if len(first_name_token) >= 3:
            search_conditions.append(Q(normalized_name__icontains=(first_name_token)))

    if probe_emails:
        search_conditions.append(
            Q(
                contact_methods__method_type="EMAIL",
                contact_methods__normalized_value__in=(probe_emails),
            )
        )

    if probe_phones:
        search_conditions.append(
            Q(
                contact_methods__method_type__in=[
                    "PHONE",
                    "MOBILE",
                    "WHATSAPP",
                ],
                contact_methods__normalized_value__in=(probe_phones),
            )
        )

    for platform_name, external_id in probe_external_sources:
        search_conditions.append(
            Q(
                sources__platform_name__iexact=platform_name,
                sources__external_id__iexact=external_id,
            )
        )

    if probe_profile_urls:
        search_conditions.append(
            Q(
                sources__profile_url__in=probe_profile_urls,
            )
        )

    if probe_listing_urls:
        search_conditions.append(
            Q(
                sources__listing_url__in=probe_listing_urls,
            )
        )

    if not search_conditions:
        return []

    combined_condition = search_conditions[0]

    for condition in search_conditions[1:]:
        combined_condition |= condition

    candidates = (
        Party.objects.filter(
            combined_condition,
        )
        .exclude(
            status=Party.Status.MERGED,
        )
        .prefetch_related(
            "contact_methods",
            "sources",
            "roles",
        )
        .distinct()
        .order_by("-updated_at")[:limit]
    )

    if exclude_party_id:
        candidates = candidates.exclude(
            pk=exclude_party_id,
        )

    results = []

    for candidate in candidates:
        candidate_emails = {
            item.normalized_value
            for item in candidate.contact_methods.all()
            if item.is_active and item.method_type == "EMAIL"
        }

        candidate_phones = {
            item.normalized_value
            for item in candidate.contact_methods.all()
            if item.is_active
            and item.method_type
            in {
                "PHONE",
                "MOBILE",
                "WHATSAPP",
            }
        }

        candidate_external_sources = {
            (
                normalize_text(
                    item.platform_name,
                ).casefold(),
                normalize_text(
                    item.external_id,
                ).casefold(),
            )
            for item in candidate.sources.all()
            if item.is_active and item.platform_name and item.external_id
        }

        candidate_profile_urls = {
            normalize_url(item.profile_url)
            for item in candidate.sources.all()
            if item.is_active and item.profile_url
        }

        candidate_listing_urls = {
            normalize_url(item.listing_url)
            for item in candidate.sources.all()
            if item.is_active and item.listing_url
        }

        exact_name = bool(display_name and candidate.normalized_name == display_name)

        email_match = bool(probe_emails & candidate_emails)

        phone_match = bool(probe_phones & candidate_phones)

        source_id_match = bool(probe_external_sources & candidate_external_sources)

        profile_match = bool(probe_profile_urls & candidate_profile_urls)

        listing_match = bool(probe_listing_urls & candidate_listing_urls)

        name_similarity = (
            SequenceMatcher(
                None,
                display_name,
                candidate.normalized_name,
            ).ratio()
            if display_name
            else 0.0
        )

        signals = []
        score = 0

        if exact_name:
            signals.append("exact_name")
            score += 40

        if email_match:
            signals.append("email")
            score += 50

        if phone_match:
            signals.append("phone")
            score += 45

        if source_id_match:
            signals.append("marketplace_seller_id")
            score += 70

        if profile_match:
            signals.append("seller_profile_url")
            score += 70

        if listing_match:
            signals.append("listing_url")
            score += 55

        if not exact_name and name_similarity >= 0.90:
            signals.append("very_similar_name")
            score += 30
        elif not exact_name and name_similarity >= 0.70:
            signals.append("similar_name")
            score += 15

        if (
            source_id_match
            or profile_match
            or (exact_name and (email_match or phone_match))
        ):
            classification = "EXACT"
        elif (
            email_match
            or phone_match
            or exact_name
            or name_similarity >= 0.88
            or listing_match
        ):
            classification = "STRONG"
        elif name_similarity >= 0.65:
            classification = "WEAK"
        else:
            continue

        results.append(
            {
                "party_id": str(candidate.id),
                "display_name": candidate.display_name,
                "entity_kind": candidate.entity_kind,
                "status": candidate.status,
                "classification": classification,
                "score": score,
                "signals": signals,
            }
        )

    classification_order = {
        "EXACT": 0,
        "STRONG": 1,
        "WEAK": 2,
    }

    return sorted(
        results,
        key=lambda item: (
            classification_order[item["classification"]],
            -item["score"],
            item["display_name"].casefold(),
        ),
    )


@transaction.atomic
def change_party_status(
    *,
    party_id: UUID | str,
    new_status: str,
    reason: str,
    user,
    request=None,
) -> Party:
    """
    Change a party's status and create status and audit history.

    Accepts:
        Party ID, target status, reason, acting user and optional request.

    Returns:
        The locked and updated Party instance.
    """

    reason = normalize_text(reason)

    if not reason:
        raise ValidationError(
            {"reason": ("A reason for the status change is required.")}
        )

    if new_status == Party.Status.MERGED:
        raise ValidationError(
            {
                "status": (
                    "MERGED status can only be applied through the merge operation."
                )
            }
        )

    party = Party.objects.select_for_update().get(
        pk=party_id,
    )

    if party.status == Party.Status.MERGED:
        raise CRMConflict("A merged party cannot have its status changed.")

    if party.status == new_status:
        return party

    previous_status = party.status

    party.status = new_status
    party.updated_by = user
    party.save(
        update_fields={
            "status",
            "updated_by",
            "updated_at",
        }
    )

    PartyStatusHistory.objects.create(
        party=party,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason,
        changed_by=user,
    )

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.UPDATE,
        resource="party",
        action="status_change",
        object_id=party.id,
        request=request,
        metadata={
            "previous_status": previous_status,
            "new_status": new_status,
            "reason": reason,
        },
    )

    return party


@transaction.atomic
def set_party_archive_state(
    *,
    party_id: UUID | str,
    archived: bool,
    reason: str,
    user,
    request=None,
) -> Party:
    """
    Archive or restore a CRM party without deleting business history.

    Accepts:
        Party ID, desired archive state, reason, actor and optional request.

    Returns:
        The locked and updated Party instance.
    """

    reason = normalize_text(reason)

    if not reason:
        raise ValidationError(
            {"reason": ("A reason for the archive change is required.")}
        )

    party = Party.objects.select_for_update().get(
        pk=party_id,
    )

    if party.status == Party.Status.MERGED:
        raise CRMConflict("A merged party cannot be restored or archived manually.")

    if party.is_archived == archived:
        return party

    party.is_archived = archived
    party.updated_by = user
    party.save(
        update_fields={
            "is_archived",
            "archived_at",
            "updated_by",
            "updated_at",
        }
    )

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.UPDATE,
        resource="party",
        action=("archive" if archived else "restore"),
        object_id=party.id,
        request=request,
        metadata={
            "archived": archived,
            "reason": reason,
        },
    )

    return party


def get_external_party_references(
    party: Party,
) -> list[dict[str, Any]]:
    """
    Discover non-CRM records that currently reference a party.

    Accepts:
        A Party instance.

    Returns:
        A list containing the referencing model, field and record count.
    """

    references = []

    for relation in party._meta.related_objects:
        related_model = relation.related_model

        if related_model._meta.app_label == "crm" or related_model._meta.auto_created:
            continue

        field_name = relation.field.name

        count = related_model._base_manager.filter(
            **{
                field_name: party,
            }
        ).count()

        if count:
            references.append(
                {
                    "model": related_model._meta.label,
                    "field": field_name,
                    "count": count,
                }
            )

    return sorted(
        references,
        key=lambda item: (
            item["model"],
            item["field"],
        ),
    )


@transaction.atomic
def delete_unused_party(
    *,
    party_id: UUID | str,
    user,
    request=None,
) -> tuple[int, dict[str, int]]:
    """
    Permanently delete an unused party after strict safety checks.

    Accepts:
        Party ID, acting user and optional request.

    Returns:
        Django's total deleted count and per-model deletion dictionary.
    """

    if not user or not user.is_superuser:
        raise PermissionDenied("Permanent CRM deletion is limited to superusers.")

    party = Party.objects.select_for_update().get(
        pk=party_id,
    )

    references = get_external_party_references(
        party,
    )

    if references:
        raise CRMConflict(
            {
                "detail": (
                    "This party is referenced by business records "
                    "and cannot be permanently deleted."
                ),
                "code": "party_has_business_references",
                "references": references,
            }
        )

    party_id_value = party.id
    party_name = party.display_name

    deletion_result = party.delete()

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.DELETE,
        resource="party",
        action="permanent_delete",
        object_id=party_id_value,
        request=request,
        metadata={
            "display_name": party_name,
        },
    )

    return deletion_result


def _merge_role_records(
    source: Party,
    target: Party,
) -> int:
    """
    Move source-party business roles to the target party.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        The number of source role records processed.
    """

    processed = 0

    for source_role in list(source.roles.all()):
        target_role, created = PartyRole.objects.get_or_create(
            party=target,
            role=source_role.role,
            defaults={
                "is_active": source_role.is_active,
                "activated_at": source_role.activated_at,
                "deactivated_at": (source_role.deactivated_at),
                "notes": source_role.notes,
            },
        )

        if not created:
            changed_fields = []

            if source_role.is_active and not target_role.is_active:
                target_role.is_active = True
                target_role.deactivated_at = None
                changed_fields.extend(
                    [
                        "is_active",
                        "deactivated_at",
                    ]
                )

            if source_role.notes and source_role.notes not in target_role.notes:
                target_role.notes = normalize_text(
                    "\n".join(
                        value
                        for value in [
                            target_role.notes,
                            source_role.notes,
                        ]
                        if value
                    )
                )
                changed_fields.append("notes")

            if changed_fields:
                target_role.save(
                    update_fields=set(
                        changed_fields
                        + [
                            "updated_at",
                        ]
                    )
                )

        source_role.delete()
        processed += 1

    return processed


def _merge_contact_methods(
    source: Party,
    target: Party,
) -> int:
    """
    Move unique contact methods and combine duplicate contact records.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        The number of source contact-method records processed.
    """

    processed = 0

    for source_method in list(source.contact_methods.all()):
        duplicate = target.contact_methods.filter(
            method_type=source_method.method_type,
            normalized_value=(source_method.normalized_value),
        ).first()

        if duplicate:
            duplicate.is_verified = duplicate.is_verified or source_method.is_verified
            duplicate.is_active = duplicate.is_active or source_method.is_active

            if (
                source_method.is_primary
                and not target.contact_methods.filter(
                    method_type=source_method.method_type,
                    is_primary=True,
                    is_active=True,
                )
                .exclude(pk=duplicate.pk)
                .exists()
            ):
                duplicate.is_primary = True

            duplicate.save(
                update_fields={
                    "is_verified",
                    "is_active",
                    "is_primary",
                    "updated_at",
                }
            )

            source_method.delete()
        else:
            if (
                source_method.is_primary
                and target.contact_methods.filter(
                    method_type=source_method.method_type,
                    is_primary=True,
                    is_active=True,
                ).exists()
            ):
                source_method.is_primary = False

            source_method.party = target
            source_method.save(
                update_fields={
                    "party",
                    "is_primary",
                    "updated_at",
                }
            )

        processed += 1

    return processed


def _merge_addresses(
    source: Party,
    target: Party,
) -> int:
    """
    Move source addresses while preserving target primary-address constraints.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        The number of source address records processed.
    """

    processed = 0

    for address in list(source.addresses.all()):
        if (
            address.is_primary
            and target.addresses.filter(
                address_type=address.address_type,
                is_primary=True,
                is_active=True,
            ).exists()
        ):
            address.is_primary = False

        address.party = target
        address.save(
            update_fields={
                "party",
                "is_primary",
                "updated_at",
            }
        )

        processed += 1

    return processed


def _merge_sources(
    source: Party,
    target: Party,
) -> int:
    """
    Move source/provenance records and preserve one target primary source.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        The number of source records processed.
    """

    processed = 0
    target_has_primary = target.sources.filter(
        is_primary=True,
        is_active=True,
    ).exists()

    for party_source in list(source.sources.all()):
        if party_source.is_primary and target_has_primary:
            party_source.is_primary = False
        elif party_source.is_primary:
            target_has_primary = True

        party_source.party = target
        party_source.save(
            update_fields={
                "party",
                "is_primary",
                "updated_at",
            }
        )

        processed += 1

    return processed


def _merge_profile_values(
    source: Party,
    target: Party,
) -> int:
    """
    Move or combine the person or organisation profile.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        One when a source profile was processed, otherwise zero.
    """

    if source.entity_kind == Party.EntityKind.INDIVIDUAL:
        relation_name = "person_profile"
        fields = [
            "title",
            "first_name",
            "middle_name",
            "last_name",
            "preferred_name",
        ]
    else:
        relation_name = "organisation_profile"
        fields = [
            "legal_name",
            "trading_name",
            "website",
            "industry",
            "business_description",
            "registration_country",
            "incorporation_date",
        ]

    try:
        source_profile = getattr(
            source,
            relation_name,
        )
    except ObjectDoesNotExist:
        return 0

    try:
        target_profile = getattr(
            target,
            relation_name,
        )
    except ObjectDoesNotExist:
        source_profile.party = target
        source_profile.save(
            update_fields={
                "party",
                "updated_at",
            }
        )
        return 1

    changed_fields = []

    for field_name in fields:
        target_value = getattr(
            target_profile,
            field_name,
        )
        source_value = getattr(
            source_profile,
            field_name,
        )

        if not target_value and source_value:
            setattr(
                target_profile,
                field_name,
                source_value,
            )
            changed_fields.append(field_name)

    if changed_fields:
        target_profile.save(
            update_fields=set(
                changed_fields
                + [
                    "updated_at",
                ]
            )
        )

    source_profile.delete()

    return 1


def _move_affiliation_roles(
    source_affiliation: PartyAffiliation,
    target_affiliation: PartyAffiliation,
) -> int:
    """
    Move contact-role assignments between duplicate affiliations.

    Accepts:
        The affiliation being removed and the surviving affiliation.

    Returns:
        The number of role assignments processed.
    """

    processed = 0

    for assignment in list(source_affiliation.role_assignments.all()):
        target_assignment, created = AffiliationContactRole.objects.get_or_create(
            affiliation=target_affiliation,
            contact_role=assignment.contact_role,
            defaults={
                "is_primary": assignment.is_primary,
            },
        )

        if not created and assignment.is_primary and not target_assignment.is_primary:
            target_assignment.is_primary = True
            target_assignment.save(
                update_fields={
                    "is_primary",
                    "updated_at",
                }
            )

        assignment.delete()
        processed += 1

    return processed


def _combine_affiliations(
    source_affiliation: PartyAffiliation,
    target_affiliation: PartyAffiliation,
) -> None:
    """
    Combine useful affiliation values before removing a duplicate affiliation.

    Accepts:
        The affiliation being removed and the surviving affiliation.

    Returns:
        None.
    """

    changed_fields = []

    for field_name in [
        "job_title",
        "department",
        "notes",
    ]:
        target_value = getattr(
            target_affiliation,
            field_name,
        )
        source_value = getattr(
            source_affiliation,
            field_name,
        )

        if not target_value and source_value:
            setattr(
                target_affiliation,
                field_name,
                source_value,
            )
            changed_fields.append(field_name)

    if (
        source_affiliation.is_primary_contact
        and not target_affiliation.is_primary_contact
    ):
        target_affiliation.is_primary_contact = True
        changed_fields.append(
            "is_primary_contact",
        )

    _move_affiliation_roles(
        source_affiliation,
        target_affiliation,
    )

    if changed_fields:
        target_affiliation.save(
            update_fields=set(
                changed_fields
                + [
                    "updated_at",
                ]
            )
        )

    source_affiliation.delete()


def _merge_affiliations(
    source: Party,
    target: Party,
) -> dict[str, int]:
    """
    Reassign organisation-person relationships from source to target.

    Accepts:
        The source Party and surviving target Party.

    Returns:
        Counts for moved, combined and removed self-affiliations.
    """

    summary = {
        "moved": 0,
        "combined": 0,
        "removed_self_relationships": 0,
    }

    if source.entity_kind == Party.EntityKind.INDIVIDUAL:
        affiliations = list(source.organisation_affiliations.all())

        for affiliation in affiliations:
            if affiliation.organisation_id == target.id:
                affiliation.delete()
                summary["removed_self_relationships"] += 1
                continue

            existing = None

            if affiliation.is_current:
                existing = PartyAffiliation.objects.filter(
                    person=target,
                    organisation=affiliation.organisation,
                    is_current=True,
                ).first()

            if existing:
                _combine_affiliations(
                    affiliation,
                    existing,
                )
                summary["combined"] += 1
            else:
                affiliation.person = target
                affiliation.save(
                    update_fields={
                        "person",
                        "updated_at",
                    }
                )
                summary["moved"] += 1

    else:
        affiliations = list(source.people_affiliations.all())

        for affiliation in affiliations:
            if affiliation.person_id == target.id:
                affiliation.delete()
                summary["removed_self_relationships"] += 1
                continue

            existing = None

            if affiliation.is_current:
                existing = PartyAffiliation.objects.filter(
                    person=affiliation.person,
                    organisation=target,
                    is_current=True,
                ).first()

            if existing:
                _combine_affiliations(
                    affiliation,
                    existing,
                )
                summary["combined"] += 1
            else:
                affiliation.organisation = target
                affiliation.save(
                    update_fields={
                        "organisation",
                        "updated_at",
                    }
                )
                summary["moved"] += 1

    return summary


def _validate_merge_parties(
    source: Party,
    target: Party,
) -> None:
    """
    Validate that two CRM parties may safely be merged.

    Accepts:
        The proposed source Party and surviving target Party.

    Returns:
        None. Raises a validation or conflict error when incompatible.
    """

    if source.id == target.id:
        raise ValidationError(
            {"target_party": ("A party cannot be merged into itself.")}
        )

    if source.status == Party.Status.MERGED:
        raise CRMConflict("The source party has already been merged.")

    if target.status == Party.Status.MERGED:
        raise CRMConflict("The selected target party has already been merged.")

    if target.is_archived:
        raise CRMConflict("An archived party cannot be selected as the merge target.")

    organisation_kinds = {
        Party.EntityKind.ORGANISATION,
        Party.EntityKind.TRADING_NAME,
    }

    compatible = source.entity_kind == target.entity_kind or {
        source.entity_kind,
        target.entity_kind,
    }.issubset(organisation_kinds)

    if not compatible:
        raise ValidationError(
            {
                "target_party": (
                    "An individual cannot be merged with "
                    "an organisation or trading name."
                )
            }
        )


@transaction.atomic
def merge_parties(
    *,
    source_party_id: UUID | str,
    target_party_id: UUID | str,
    reason: str,
    user,
    request=None,
) -> tuple[Party, PartyMergeRecord]:
    """
    Merge one CRM party into another while preserving the source tombstone.

    Accepts:
        Source party ID, target party ID, reason, actor and optional request.

    Returns:
        A tuple containing the surviving Party and PartyMergeRecord.
    """

    reason = normalize_text(reason)

    if not reason:
        raise ValidationError(
            {
                "reason": "A reason for the merge is required.",
            }
        )

    lock_ids = sorted(
        [
            str(source_party_id),
            str(target_party_id),
        ]
    )

    locked_parties = {
        str(party.id): party
        for party in Party.objects.select_for_update()
        .filter(
            id__in=lock_ids,
        )
        .prefetch_related(
            "roles",
            "contact_methods",
            "addresses",
            "sources",
            "organisation_affiliations__role_assignments",
            "people_affiliations__role_assignments",
        )
    }

    source = locked_parties.get(
        str(source_party_id),
    )
    target = locked_parties.get(
        str(target_party_id),
    )

    if source is None or target is None:
        raise ValidationError(
            {"detail": ("The source or target CRM party does not exist.")}
        )

    _validate_merge_parties(
        source,
        target,
    )

    summary = {
        "roles": _merge_role_records(
            source,
            target,
        ),
        "contact_methods": _merge_contact_methods(
            source,
            target,
        ),
        "addresses": _merge_addresses(
            source,
            target,
        ),
        "sources": _merge_sources(
            source,
            target,
        ),
        "profiles": _merge_profile_values(
            source,
            target,
        ),
        "affiliations": _merge_affiliations(
            source,
            target,
        ),
    }
    # summary["external_references"] = reassign_external_party_references(
    #     source=source,
    #     target=target,
    # )

    summary["notes"] = PartyNote.objects.filter(
        party=source,
    ).update(
        party=target,
    )

    summary["interactions"] = PartyInteraction.objects.filter(
        party=source,
    ).update(
        party=target,
    )

    summary["contact_interactions"] = PartyInteraction.objects.filter(
        contact_party=source,
    ).update(
        contact_party=target,
    )

    previous_status = source.status

    source.status = Party.Status.MERGED
    source.merged_into = target
    source.is_archived = True
    source.archived_at = timezone.now()
    source.updated_by = user
    source.save(
        update_fields={
            "status",
            "merged_into",
            "is_archived",
            "archived_at",
            "updated_by",
            "updated_at",
        }
    )

    target.updated_by = user
    target.save(
        update_fields={
            "updated_by",
            "updated_at",
        }
    )

    PartyStatusHistory.objects.create(
        party=source,
        previous_status=previous_status,
        new_status=Party.Status.MERGED,
        reason=reason,
        changed_by=user,
        metadata={
            "target_party_id": str(target.id),
        },
    )

    merge_record = PartyMergeRecord.objects.create(
        source_party=source,
        target_party=target,
        reason=reason,
        merged_by=user,
        summary=summary,
    )

    log_crm_event(
        user=user,
        event_type=AuditLog.EventType.UPDATE,
        resource="party",
        action="merge",
        object_id=source.id,
        request=request,
        metadata={
            "source_party_id": str(source.id),
            "target_party_id": str(target.id),
            "reason": reason,
            "summary": summary,
        },
    )

    return target, merge_record
