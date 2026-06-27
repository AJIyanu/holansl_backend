from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from notifications.constants import NotificationChannel
from notifications.models import NotificationPreference


def get_notification_preference(
    user,
    *,
    category,
    notification_type,
):
    """
    Return the most specific applicable preference.

    Preference order:
    1. Exact category and notification type
    2. Category default
    3. Notification-type default
    4. Global default
    """

    scopes = [
        (category, notification_type),
        (category, ""),
        ("", notification_type),
        ("", ""),
    ]

    for scoped_category, scoped_type in scopes:
        preference = NotificationPreference.objects.filter(
            user=user,
            category=scoped_category,
            notification_type=scoped_type,
        ).first()

        if preference:
            return preference

    return None


def is_channel_enabled(
    preference,
    channel,
    *,
    is_mandatory=False,
):
    if is_mandatory or preference is None:
        return True

    return preference.channel_enabled(channel)


def defer_until_after_quiet_hours(
    scheduled_at,
    preference,
    *,
    channel,
    is_mandatory=False,
):
    """
    Move external delivery to the end of the user's quiet period.
    Dashboard notifications remain immediately available.
    """

    if (
        is_mandatory
        or preference is None
        or channel == NotificationChannel.DASHBOARD
        or not preference.quiet_hours_start
        or not preference.quiet_hours_end
    ):
        return scheduled_at

    try:
        user_timezone = ZoneInfo(preference.timezone_name)
    except ZoneInfoNotFoundError:
        user_timezone = ZoneInfo("UTC")

    current = scheduled_at.astimezone(user_timezone)

    current_time = current.timetz().replace(tzinfo=None)

    start = preference.quiet_hours_start
    end = preference.quiet_hours_end

    if start == end:
        return scheduled_at

    if start < end:
        in_quiet_hours = start <= current_time < end
        end_date = current.date()
    else:
        in_quiet_hours = current_time >= start or current_time < end

        end_date = current.date() + (
            timedelta(days=1) if current_time >= start else timedelta()
        )

    if not in_quiet_hours:
        return scheduled_at

    local_end = datetime.combine(
        end_date,
        end,
        tzinfo=user_timezone,
    )

    return local_end.astimezone(timezone.get_current_timezone())
