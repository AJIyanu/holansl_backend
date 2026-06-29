from django.conf import settings
from django.core.checks import (
    Error,
    Tags,
    Warning,
    register,
)

from notifications.constants import (
    NotificationChannel,
    NotificationEventMode,
)


def as_setting_list(value):
    if isinstance(value, str):
        return [item.strip().upper() for item in value.split(",") if item.strip()]

    return [str(item).strip().upper() for item in (value or []) if str(item).strip()]


@register(Tags.compatibility)
def check_task_configuration(
    app_configs,
    **kwargs,
):
    messages = []

    valid_channels = {value for value, _label in NotificationChannel.choices}

    channel_settings = {
        "TASK_ASSIGNMENT_NOTIFICATION_CHANNELS": getattr(
            settings,
            "TASK_ASSIGNMENT_NOTIFICATION_CHANNELS",
            [],
        ),
        "TASK_LIFECYCLE_NOTIFICATION_CHANNELS": getattr(
            settings,
            "TASK_LIFECYCLE_NOTIFICATION_CHANNELS",
            [],
        ),
    }

    for setting_name, raw_value in channel_settings.items():
        configured_channels = as_setting_list(raw_value)

        invalid_channels = sorted(set(configured_channels) - valid_channels)

        if invalid_channels:
            messages.append(
                Error(
                    (
                        f"{setting_name} contains "
                        "unsupported channels: "
                        f"{', '.join(invalid_channels)}."
                    ),
                    id="tasks.E001",
                )
            )

    configured_event_mode = str(
        getattr(
            settings,
            "TASK_ASSIGNMENT_NOTIFICATION_EVENT_MODE",
            NotificationEventMode.SHARED,
        )
    ).upper()

    valid_event_modes = {value for value, _label in NotificationEventMode.choices}

    if configured_event_mode not in valid_event_modes:
        messages.append(
            Error(
                (
                    "TASK_ASSIGNMENT_NOTIFICATION_EVENT_MODE "
                    "must be SHARED or INDIVIDUAL."
                ),
                id="tasks.E002",
            )
        )

    reminders_enabled = getattr(
        settings,
        "TASK_REMINDERS_ENABLED",
        True,
    )

    dashboard_enabled = getattr(
        settings,
        "TASK_DASHBOARD_REMINDERS_ENABLED",
        True,
    )

    external_enabled = getattr(
        settings,
        "TASK_SCHEDULED_EXTERNAL_DELIVERY_ENABLED",
        False,
    )

    if reminders_enabled and not dashboard_enabled and not external_enabled:
        messages.append(
            Warning(
                (
                    "Task reminders are enabled, but no "
                    "scheduled reminder channel is enabled."
                ),
                hint=(
                    "Enable dashboard reminders or configure "
                    "scheduled external delivery."
                ),
                id="tasks.W001",
            )
        )

    return messages
