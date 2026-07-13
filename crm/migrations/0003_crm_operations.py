"""
Create CRM notes, interactions, status history and merge history.

This migration accepts the schema state produced by crm.0002_crm_foundation
and returns a database schema supporting Stage 3 business operations.
"""

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Apply the CRM Stage 3 operational-history schema.

    Accepts:
        Django's migration application state.

    Returns:
        Updated database tables, constraints, indexes and permissions.
    """

    dependencies = [
        migrations.swappable_dependency(
            settings.AUTH_USER_MODEL,
        ),
        (
            "crm",
            "0002_crm_foundation",
        ),
    ]

    operations = [
        migrations.CreateModel(
            name="PartyNote",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "note_type",
                    models.CharField(
                        choices=[
                            ("GENERAL", "General"),
                            (
                                "PROCUREMENT",
                                "Procurement",
                            ),
                            ("ACCOUNTS", "Accounts"),
                            ("RISK", "Risk"),
                            (
                                "CONFIDENTIAL",
                                "Confidential",
                            ),
                        ],
                        db_index=True,
                        default="GENERAL",
                        max_length=20,
                    ),
                ),
                (
                    "content",
                    models.TextField(),
                ),
                (
                    "is_confidential",
                    models.BooleanField(
                        db_index=True,
                        default=False,
                    ),
                ),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name="crm_notes_authored",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.CASCADE),
                        related_name="notes",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "-created_at",
                ],
                "permissions": [
                    (
                        "view_confidentialnote",
                        "Can view confidential CRM notes",
                    ),
                    (
                        "manage_confidentialnote",
                        "Can manage confidential CRM notes",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "party",
                            "-created_at",
                        ],
                        name=("crm_note_party_created_idx"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PartyInteraction",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "interaction_type",
                    models.CharField(
                        choices=[
                            ("CALL", "Call"),
                            ("EMAIL", "Email"),
                            (
                                "WHATSAPP",
                                "WhatsApp",
                            ),
                            ("MEETING", "Meeting"),
                            (
                                "MARKETPLACE_MESSAGE",
                                "Marketplace message",
                            ),
                            (
                                "SITE_VISIT",
                                "Site visit",
                            ),
                            ("OTHER", "Other"),
                        ],
                        db_index=True,
                        max_length=30,
                    ),
                ),
                (
                    "occurred_at",
                    models.DateTimeField(
                        db_index=True,
                        default=(django.utils.timezone.now),
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "summary",
                    models.TextField(),
                ),
                (
                    "follow_up_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                    ),
                ),
                (
                    "contact_party",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Optional individual CRM party "
                            "who participated in the interaction."
                        ),
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name=("contact_interactions"),
                        to="crm.party",
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name="interactions",
                        to="crm.party",
                    ),
                ),
                (
                    "staff_member",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name=("crm_interactions_recorded"),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": [
                    "-occurred_at",
                    "-created_at",
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "party",
                            "-occurred_at",
                        ],
                        name=("crm_interaction_party_idx"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PartyStatusHistory",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "previous_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("ACTIVE", "Active"),
                            ("INACTIVE", "Inactive"),
                            (
                                "SUSPENDED",
                                "Suspended",
                            ),
                            ("BLOCKED", "Blocked"),
                            ("MERGED", "Merged"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "new_status",
                    models.CharField(
                        choices=[
                            ("ACTIVE", "Active"),
                            ("INACTIVE", "Inactive"),
                            (
                                "SUSPENDED",
                                "Suspended",
                            ),
                            ("BLOCKED", "Blocked"),
                            ("MERGED", "Merged"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "reason",
                    models.TextField(),
                ),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                    ),
                ),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name=("crm_status_changes"),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name="status_history",
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "-created_at",
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "party",
                            "-created_at",
                        ],
                        name=("crm_status_party_created_idx"),
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PartyMergeRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                    ),
                ),
                (
                    "reason",
                    models.TextField(),
                ),
                (
                    "summary",
                    models.JSONField(
                        blank=True,
                        default=dict,
                    ),
                ),
                (
                    "merged_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=(django.db.models.deletion.SET_NULL),
                        related_name=("crm_merges_performed"),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_party",
                    models.OneToOneField(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name=("merge_record_as_source"),
                        to="crm.party",
                    ),
                ),
                (
                    "target_party",
                    models.ForeignKey(
                        on_delete=(django.db.models.deletion.PROTECT),
                        related_name=("merge_records_as_target"),
                        to="crm.party",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "-created_at",
                ],
                "indexes": [
                    models.Index(
                        fields=[
                            "target_party",
                            "-created_at",
                        ],
                        name=("crm_merge_target_created_idx"),
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(source_party=models.F("target_party")),
                        name=("crm_merge_distinct_parties"),
                    ),
                ],
            },
        ),
    ]
