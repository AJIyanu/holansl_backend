import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .constants import (
    DeliveryStatus,
    NotificationChannel,
    NotificationSeverity,
)


class Notification(models.Model):
    """
    Generic event produced by tasks, accounts, procurement,
    security or any other backend module.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    notification_type = models.CharField(
        max_length=120,
        db_index=True,
    )

    category = models.SlugField(
        max_length=60,
        db_index=True,
    )

    severity = models.CharField(
        max_length=20,
        choices=NotificationSeverity.choices,
        default=NotificationSeverity.INFO,
        db_index=True,
    )

    title = models.CharField(max_length=255)
    message = models.TextField()

    template_key = models.SlugField(
        max_length=120,
        blank=True,
    )

    language = models.CharField(
        max_length=12,
        default="en",
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_notifications",
    )

    source_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_notifications",
    )

    source_object_id = models.CharField(
        max_length=255,
        blank=True,
    )

    source_object = GenericForeignKey(
        "source_content_type",
        "source_object_id",
        for_concrete_model=False,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    deduplication_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )

    is_mandatory = models.BooleanField(default=False)

    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

        permissions = [
            (
                "dispatch_notification",
                "Can dispatch notifications",
            ),
            (
                "view_all_notification",
                "Can view all notifications",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "category",
                    "notification_type",
                    "-created_at",
                ],
                name="notif_cat_type_created_idx",
            ),
            models.Index(
                fields=[
                    "source_content_type",
                    "source_object_id",
                ],
                name="notif_source_idx",
            ),
            models.Index(
                fields=["expires_at"],
                name="notif_expires_idx",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.notification_type})"

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())


class NotificationRecipient(models.Model):
    """
    User-specific inbox state.

    A shared Notification event can have many recipient rows.
    Each recipient independently reads, archives or dismisses it.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="recipients",
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_recipients",
    )

    action_url = models.CharField(
        max_length=500,
        blank=True,
    )

    action_label = models.CharField(
        max_length=100,
        blank=True,
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    seen_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    archived_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "notification",
                    "recipient",
                ],
                name="uniq_notification_recipient",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "recipient",
                    "read_at",
                    "-created_at",
                ],
                name="notif_recipient_read_idx",
            ),
            models.Index(
                fields=[
                    "recipient",
                    "archived_at",
                    "-created_at",
                ],
                name="notif_recipient_archive_idx",
            ),
        ]

    def __str__(self):
        return f"{self.recipient} - {self.notification.title}"

    def mark_seen(self):
        if self.seen_at is None:
            self.seen_at = timezone.now()
            self.save(update_fields=["seen_at"])

    def mark_read(self):
        now = timezone.now()
        update_fields = []

        if self.seen_at is None:
            self.seen_at = now
            update_fields.append("seen_at")

        if self.read_at is None:
            self.read_at = now
            update_fields.append("read_at")

        if update_fields:
            self.save(update_fields=update_fields)

            self.deliveries.filter(channel=NotificationChannel.DASHBOARD).update(
                status=DeliveryStatus.READ,
                read_at=now,
                updated_at=now,
            )


class NotificationPreference(models.Model):
    """
    Per-user delivery preferences.

    Empty category and notification_type values act as wildcards.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )

    category = models.SlugField(
        max_length=60,
        blank=True,
        default="",
    )

    notification_type = models.CharField(
        max_length=120,
        blank=True,
        default="",
    )

    dashboard_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    whatsapp_enabled = models.BooleanField(default=False)

    quiet_hours_start = models.TimeField(
        null=True,
        blank=True,
    )

    quiet_hours_end = models.TimeField(
        null=True,
        blank=True,
    )

    timezone_name = models.CharField(
        max_length=64,
        default="UTC",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "user_id",
            "category",
            "notification_type",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "category",
                    "notification_type",
                ],
                name="uniq_notification_preference_scope",
            )
        ]

    def __str__(self):
        scope = self.notification_type or self.category or "all notifications"
        return f"{self.user} - {scope}"

    def clean(self):
        if bool(self.quiet_hours_start) != bool(self.quiet_hours_end):
            raise ValidationError(
                "Both quiet_hours_start and quiet_hours_end are required."
            )

    def channel_enabled(self, channel):
        return {
            NotificationChannel.DASHBOARD: self.dashboard_enabled,
            NotificationChannel.EMAIL: self.email_enabled,
            NotificationChannel.WHATSAPP: self.whatsapp_enabled,
        }[channel]


class NotificationTemplate(models.Model):
    """Versioned database templates for each channel."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    key = models.SlugField(max_length=120)
    name = models.CharField(max_length=150)

    channel = models.CharField(
        max_length=20,
        choices=NotificationChannel.choices,
    )

    language = models.CharField(
        max_length=12,
        default="en",
    )

    version = models.PositiveIntegerField(default=1)

    subject_template = models.CharField(
        max_length=255,
        blank=True,
    )

    title_template = models.CharField(
        max_length=255,
        blank=True,
    )

    body_text_template = models.TextField(blank=True)
    body_html_template = models.TextField(blank=True)

    action_label_template = models.CharField(
        max_length=100,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "key",
            "channel",
            "-version",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "key",
                    "channel",
                    "language",
                    "version",
                ],
                name="uniq_notification_template_version",
            ),
            models.UniqueConstraint(
                fields=[
                    "key",
                    "channel",
                    "language",
                ],
                condition=Q(is_active=True),
                name="uniq_active_notification_template",
            ),
        ]

        permissions = [
            (
                "manage_notificationtemplate",
                "Can manage notification templates",
            ),
        ]

    def __str__(self):
        return f"{self.key} - {self.channel} - v{self.version}"

    def clean(self):
        if self.channel == NotificationChannel.EMAIL and not self.subject_template:
            raise ValidationError(
                {"subject_template": "Email templates require a subject."}
            )

        if self.channel == NotificationChannel.DASHBOARD and not self.title_template:
            raise ValidationError(
                {"title_template": "Dashboard templates require a title."}
            )

        if not self.body_text_template and not self.body_html_template:
            raise ValidationError("At least one body template is required.")


class NotificationDelivery(models.Model):
    """
    Transactional outbox row for one recipient and one channel.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    notification_recipient = models.ForeignKey(
        NotificationRecipient,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )

    channel = models.CharField(
        max_length=20,
        choices=NotificationChannel.choices,
    )

    status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        db_index=True,
    )

    destination = models.CharField(
        max_length=255,
        blank=True,
    )

    provider = models.CharField(
        max_length=80,
        blank=True,
    )

    provider_message_id = models.CharField(
        max_length=255,
        blank=True,
    )

    subject = models.CharField(
        max_length=255,
        blank=True,
    )

    title = models.CharField(
        max_length=255,
        blank=True,
    )

    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)

    payload = models.JSONField(
        default=dict,
        blank=True,
    )

    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)

    scheduled_at = models.DateTimeField(
        default=timezone.now,
    )

    next_attempt_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    locked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    locked_by = models.CharField(
        max_length=120,
        blank=True,
    )

    sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    error_code = models.CharField(
        max_length=100,
        blank=True,
    )

    error_message = models.TextField(blank=True)

    response_metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "notification_recipient",
                    "channel",
                ],
                name="uniq_recipient_delivery_channel",
            )
        ]

        permissions = [
            (
                "retry_notificationdelivery",
                "Can retry notification deliveries",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "status",
                    "next_attempt_at",
                ],
                name="notif_delivery_due_idx",
            ),
            models.Index(
                fields=[
                    "channel",
                    "status",
                    "-created_at",
                ],
                name="notif_delivery_chan_idx",
            ),
            models.Index(
                fields=[
                    "provider",
                    "provider_message_id",
                ],
                name="notif_delivery_provider_idx",
            ),
        ]

    def __str__(self):
        return f"{self.channel} - {self.status} - {self.notification_recipient}"
