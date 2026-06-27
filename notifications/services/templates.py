from django.conf import settings
from django.template import Context, Template
from django.utils.html import escape
from django.utils.text import Truncator

from notifications.constants import NotificationChannel
from notifications.models import NotificationTemplate


def get_active_template(
    *,
    key,
    channel,
    language=None,
):
    if not key:
        return None

    language = language or getattr(
        settings,
        "NOTIFICATION_DEFAULT_LANGUAGE",
        "en",
    )

    notification_template = (
        NotificationTemplate.objects.filter(
            key=key,
            channel=channel,
            language=language,
            is_active=True,
        )
        .order_by("-version")
        .first()
    )

    if notification_template or language == "en":
        return notification_template

    return (
        NotificationTemplate.objects.filter(
            key=key,
            channel=channel,
            language="en",
            is_active=True,
        )
        .order_by("-version")
        .first()
    )


def render_string(template_string, context):
    if not template_string:
        return ""

    return (
        Template(template_string)
        .render(
            Context(
                context,
                autoescape=True,
            )
        )
        .strip()
    )


def render_delivery_content(
    *,
    template_key,
    channel,
    language,
    context,
    fallback_title,
    fallback_message,
    fallback_action_label="",
):
    notification_template = get_active_template(
        key=template_key,
        channel=channel,
        language=language,
    )

    if notification_template:
        return {
            "subject": render_string(
                notification_template.subject_template,
                context,
            ),
            "title": (
                render_string(
                    notification_template.title_template,
                    context,
                )
                or fallback_title
            ),
            "body_text": (
                render_string(
                    notification_template.body_text_template,
                    context,
                )
                or fallback_message
            ),
            "body_html": render_string(
                notification_template.body_html_template,
                context,
            ),
            "action_label": (
                render_string(
                    notification_template.action_label_template,
                    context,
                )
                or fallback_action_label
            ),
            "template_id": str(notification_template.id),
            "template_version": notification_template.version,
        }

    fallback_html = "<p>{}</p>".format(
        escape(fallback_message).replace(
            "\n",
            "<br>",
        )
    )

    return {
        "subject": (fallback_title if channel == NotificationChannel.EMAIL else ""),
        "title": fallback_title,
        "body_text": fallback_message,
        "body_html": (fallback_html if channel == NotificationChannel.EMAIL else ""),
        "action_label": fallback_action_label,
        "template_id": "",
        "template_version": None,
    }


def render_event_content(
    *,
    template_key,
    language,
    context,
    fallback_title,
    fallback_message,
):
    content = render_delivery_content(
        template_key=template_key,
        channel=NotificationChannel.DASHBOARD,
        language=language,
        context=context,
        fallback_title=fallback_title,
        fallback_message=fallback_message,
    )

    title = Truncator(content["title"] or fallback_title).chars(255)

    message = content["body_text"] or fallback_message

    return title, message
