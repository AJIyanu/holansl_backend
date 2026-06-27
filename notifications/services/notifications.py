from collections.abc import Iterable

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from notifications.constants import (
    DeliveryStatus,
    NotificationChannel,
    NotificationEventMode,
    NotificationSeverity,
)
from notifications.data import (
    NotificationResult,
    RecipientSpec,
)
from notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationRecipient,
)
from notifications.services.delivery import (
    schedule_delivery_processing,
)
from notifications.services.preferences import (
    defer_until_after_quiet_hours,
    get_notification_preference,
    is_channel_enabled,
)
from notifications.services.providers import (
    channel_is_configured,
)
from notifications.services.templates import (
    render_delivery_content,
    render_event_content,
)


def _normalise_recipients(recipients):
    normalised = []
    seen_user_ids = set()

    for item in recipients:
        spec = item if isinstance(item, RecipientSpec) else RecipientSpec(user=item)

        user = spec.user

        if not getattr(user, "pk", None):
            raise ValueError("Every notification recipient must be a saved user.")

        if not user.is_active:
            raise ValueError(f"Inactive user {user.pk} cannot receive notifications.")

        if user.pk in seen_user_ids:
            continue

        seen_user_ids.add(user.pk)
        normalised.append(spec)

    if not normalised:
        raise ValueError("At least one active notification recipient is required.")

    return normalised


def _normalise_channels(channels):
    valid_channels = {choice for choice, _label in NotificationChannel.choices}

    normalised = []

    for channel in channels:
        channel = str(channel).upper()

        if channel not in valid_channels:
            raise ValueError(f"Unsupported notification channel: {channel}")

        if channel not in normalised:
            normalised.append(channel)

    if not normalised:
        raise ValueError("At least one notification channel is required.")

    return normalised


def _source_values(source):
    if source is None:
        return None, ""

    if not getattr(source, "pk", None):
        raise ValueError("The notification source must be a saved model instance.")

    content_type = ContentType.objects.get_for_model(
        source,
        for_concrete_model=False,
    )

    return content_type, str(source.pk)


def _recipient_destination(user, channel):
    if channel == NotificationChannel.EMAIL:
        return user.email or ""

    if channel == NotificationChannel.WHATSAPP:
        profile = getattr(user, "profile", None)

        return (
            getattr(
                profile,
                "phone_number",
                "",
            )
            or ""
        )

    return ""


def _base_template_context(
    *,
    actor,
    title,
    message,
    category,
    notification_type,
    metadata,
):
    return {
        "actor": actor,
        "title": title,
        "message": message,
        "category": category,
        "notification_type": notification_type,
        "metadata": metadata or {},
        "frontend_url": settings.FRONTEND_URL.rstrip("/"),
    }


def _create_notification(
    *,
    notification_type,
    category,
    severity,
    title,
    message,
    template_key,
    language,
    actor,
    source_content_type,
    source_object_id,
    metadata,
    deduplication_key,
    is_mandatory,
    scheduled_at,
    expires_at,
    template_context,
):
    rendered_title, rendered_message = render_event_content(
        template_key=template_key,
        language=language,
        context=template_context,
        fallback_title=title,
        fallback_message=message,
    )

    if not rendered_title or not rendered_message:
        raise ValueError(
            "Notification title and message are "
            "required when no active template "
            "provides them."
        )

    return Notification.objects.create(
        notification_type=notification_type,
        category=category,
        severity=severity,
        title=rendered_title,
        message=rendered_message,
        template_key=template_key or "",
        language=language,
        actor=actor,
        source_content_type=source_content_type,
        source_object_id=source_object_id,
        metadata=metadata or {},
        deduplication_key=deduplication_key,
        is_mandatory=is_mandatory,
        scheduled_at=scheduled_at,
        expires_at=expires_at,
    )


def _existing_result(deduplication_key):
    if not deduplication_key:
        return None

    notification = Notification.objects.filter(
        deduplication_key=deduplication_key
    ).first()

    if notification is None:
        return None

    recipient_ids = list(
        notification.recipients.values_list(
            "id",
            flat=True,
        )
    )

    delivery_ids = list(
        NotificationDelivery.objects.filter(
            notification_recipient__notification=notification
        ).values_list(
            "id",
            flat=True,
        )
    )

    return NotificationResult(
        notification_ids=[notification.id],
        recipient_ids=recipient_ids,
        delivery_ids=delivery_ids,
        created=False,
    )


def _create_recipient_deliveries(
    *,
    notification,
    spec,
    channels,
    template_key,
    language,
    base_context,
    scheduled_at,
):
    recipient = NotificationRecipient.objects.create(
        notification=notification,
        recipient=spec.user,
        action_url=spec.action_url,
        action_label=spec.action_label,
        metadata=spec.metadata or {},
    )

    preference = get_notification_preference(
        spec.user,
        category=notification.category,
        notification_type=notification.notification_type,
    )

    frontend_url = settings.FRONTEND_URL.rstrip("/")

    action_link = (
        spec.action_url
        if spec.action_url.startswith(("http://", "https://"))
        else (f"{frontend_url}{spec.action_url}" if spec.action_url else "")
    )

    context = {
        **base_context,
        **(spec.template_context or {}),
        "recipient": spec.user,
        "recipient_name": (spec.user.get_full_name() or spec.user.username),
        "notification": notification,
        "action_url": spec.action_url,
        "action_link": action_link,
        "action_label": spec.action_label,
        "recipient_metadata": spec.metadata or {},
    }

    deliveries = []

    for channel in channels:
        enabled = is_channel_enabled(
            preference,
            channel,
            is_mandatory=notification.is_mandatory,
        )

        configured = channel_is_configured(channel)

        destination = _recipient_destination(
            spec.user,
            channel,
        )

        content = render_delivery_content(
            template_key=template_key,
            channel=channel,
            language=language,
            context=context,
            fallback_title=notification.title,
            fallback_message=notification.message,
            fallback_action_label=spec.action_label,
        )

        delivery_schedule = defer_until_after_quiet_hours(
            scheduled_at,
            preference,
            channel=channel,
            is_mandatory=notification.is_mandatory,
        )

        status = DeliveryStatus.PENDING
        error_code = ""
        error_message = ""
        delivered_at = None

        if not enabled:
            status = DeliveryStatus.SKIPPED
            error_code = "disabled_by_preference"
            error_message = "The recipient disabled this notification channel."

        elif channel != NotificationChannel.DASHBOARD and not destination:
            status = DeliveryStatus.SKIPPED
            error_code = "missing_destination"
            error_message = "The recipient has no destination for this channel."

        elif not configured:
            status = DeliveryStatus.SKIPPED
            error_code = "channel_not_configured"
            error_message = "This notification channel is not configured."

        elif channel == NotificationChannel.DASHBOARD:
            status = DeliveryStatus.DELIVERED
            delivered_at = timezone.now()

        delivery = NotificationDelivery.objects.create(
            notification_recipient=recipient,
            channel=channel,
            status=status,
            destination=destination,
            provider=(
                "dashboard"
                if channel == NotificationChannel.DASHBOARD
                else (
                    "resend"
                    if channel == NotificationChannel.EMAIL
                    else getattr(
                        settings,
                        "NOTIFICATION_WHATSAPP_PROVIDER",
                        "disabled",
                    )
                )
            ),
            subject=content["subject"],
            title=content["title"],
            body_text=content["body_text"],
            body_html=content["body_html"],
            payload={
                "action_url": spec.action_url,
                "action_label": content["action_label"],
                "template_id": content["template_id"],
                "template_version": content["template_version"],
            },
            max_attempts=getattr(
                settings,
                "NOTIFICATION_DEFAULT_MAX_ATTEMPTS",
                3,
            ),
            scheduled_at=delivery_schedule,
            next_attempt_at=delivery_schedule,
            delivered_at=delivered_at,
            error_code=error_code,
            error_message=error_message,
        )

        deliveries.append(delivery)

    return recipient, deliveries


def notify(
    *,
    recipients: Iterable,
    notification_type,
    category,
    title,
    message,
    channels=(NotificationChannel.DASHBOARD,),
    event_mode=NotificationEventMode.SHARED,
    severity=NotificationSeverity.INFO,
    actor=None,
    source=None,
    template_key="",
    language=None,
    metadata=None,
    template_context=None,
    deduplication_key=None,
    is_mandatory=False,
    scheduled_at=None,
    expires_at=None,
):
    """
    Create generic notification events and delivery rows.

    SHARED:
        One Notification with many NotificationRecipient rows.

    INDIVIDUAL:
        One Notification per recipient.
    """

    specs = _normalise_recipients(recipients)
    channels = _normalise_channels(channels)

    valid_event_modes = {choice for choice, _label in NotificationEventMode.choices}

    if event_mode not in valid_event_modes:
        raise ValueError(f"Unsupported event mode: {event_mode}")

    language = language or getattr(
        settings,
        "NOTIFICATION_DEFAULT_LANGUAGE",
        "en",
    )

    scheduled_at = scheduled_at or timezone.now()

    if expires_at and expires_at <= scheduled_at:
        raise ValueError("expires_at must be later than scheduled_at.")

    (
        source_content_type,
        source_object_id,
    ) = _source_values(source)

    metadata = metadata or {}

    base_context = {
        **_base_template_context(
            actor=actor,
            title=title,
            message=message,
            category=category,
            notification_type=notification_type,
            metadata=metadata,
        ),
        **(template_context or {}),
    }

    if event_mode == NotificationEventMode.SHARED:
        existing = _existing_result(deduplication_key)

        if existing:
            return existing

    notification_ids = []
    recipient_ids = []
    delivery_ids = []
    newly_created = False

    with transaction.atomic():
        shared_notification = None

        if event_mode == NotificationEventMode.SHARED:
            shared_notification = _create_notification(
                notification_type=notification_type,
                category=category,
                severity=severity,
                title=title,
                message=message,
                template_key=template_key,
                language=language,
                actor=actor,
                source_content_type=source_content_type,
                source_object_id=source_object_id,
                metadata=metadata,
                deduplication_key=deduplication_key,
                is_mandatory=is_mandatory,
                scheduled_at=scheduled_at,
                expires_at=expires_at,
                template_context=base_context,
            )

            notification_ids.append(shared_notification.id)

            newly_created = True

        for spec in specs:
            notification = shared_notification

            individual_context = {
                **base_context,
                **(spec.template_context or {}),
                "recipient": spec.user,
                "recipient_name": (spec.user.get_full_name() or spec.user.username),
                "action_url": spec.action_url,
                "action_link": (
                    spec.action_url
                    if spec.action_url.startswith(("http://", "https://"))
                    else (
                        f"{settings.FRONTEND_URL.rstrip('/')}{spec.action_url}"
                        if spec.action_url
                        else ""
                    )
                ),
                "action_label": spec.action_label,
            }

            if event_mode == NotificationEventMode.INDIVIDUAL:
                individual_key = (
                    f"{deduplication_key}:{spec.user.pk}" if deduplication_key else None
                )

                existing = _existing_result(individual_key)

                if existing:
                    notification_ids.extend(existing.notification_ids)

                    recipient_ids.extend(existing.recipient_ids)

                    delivery_ids.extend(existing.delivery_ids)

                    continue

                notification = _create_notification(
                    notification_type=notification_type,
                    category=category,
                    severity=severity,
                    title=title,
                    message=message,
                    template_key=template_key,
                    language=language,
                    actor=actor,
                    source_content_type=source_content_type,
                    source_object_id=source_object_id,
                    metadata=metadata,
                    deduplication_key=individual_key,
                    is_mandatory=is_mandatory,
                    scheduled_at=scheduled_at,
                    expires_at=expires_at,
                    template_context=individual_context,
                )

                notification_ids.append(notification.id)

                newly_created = True

            recipient, deliveries = _create_recipient_deliveries(
                notification=notification,
                spec=spec,
                channels=channels,
                template_key=template_key,
                language=language,
                base_context=base_context,
                scheduled_at=scheduled_at,
            )

            recipient_ids.append(recipient.id)

            delivery_ids.extend(delivery.id for delivery in deliveries)

        pending_external_ids = list(
            NotificationDelivery.objects.filter(
                id__in=delivery_ids,
                status=DeliveryStatus.PENDING,
            )
            .exclude(channel=NotificationChannel.DASHBOARD)
            .values_list(
                "id",
                flat=True,
            )
        )

        schedule_delivery_processing(pending_external_ids)

    return NotificationResult(
        notification_ids=notification_ids,
        recipient_ids=recipient_ids,
        delivery_ids=delivery_ids,
        created=newly_created,
    )
