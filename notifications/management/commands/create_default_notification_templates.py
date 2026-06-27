from django.core.management.base import BaseCommand
from django.db import transaction

from notifications.constants import (
    NotificationChannel,
)
from notifications.models import (
    NotificationTemplate,
)


TEMPLATES = [
    {
        "key": "general.message",
        "name": "General dashboard message",
        "channel": NotificationChannel.DASHBOARD,
        "subject_template": "",
        "title_template": "{{ title }}",
        "body_text_template": "{{ message }}",
        "body_html_template": "",
        "action_label_template": "{{ action_label }}",
    },
    {
        "key": "general.message",
        "name": "General email message",
        "channel": NotificationChannel.EMAIL,
        "subject_template": "{{ title }}",
        "title_template": "{{ title }}",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            "{{ message }}\n\n"
            "{% if action_url %}"
            "Open: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            "<p>{{ message }}</p>"
            "{% if action_url %}"
            "<p>"
            '<a href="{{ action_link }}">'
            "{{ action_label|default:'Open' }}"
            "</a>"
            "</p>"
            "{% endif %}"
        ),
        "action_label_template": "{{ action_label }}",
    },
    {
        "key": "general.message",
        "name": "General WhatsApp message",
        "channel": NotificationChannel.WHATSAPP,
        "subject_template": "",
        "title_template": "{{ title }}",
        "body_text_template": (
            "{{ title }}\n\n"
            "{{ message }}"
            "{% if action_url %}"
            "\n{{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": "",
        "action_label_template": "{{ action_label }}",
    },
]


class Command(BaseCommand):
    help = "Creates the reusable default notification templates."

    @transaction.atomic
    def handle(self, *args, **options):
        for item in TEMPLATES:
            NotificationTemplate.objects.filter(
                key=item["key"],
                channel=item["channel"],
                language="en",
                is_active=True,
            ).exclude(version=1).update(is_active=False)

            template, created = NotificationTemplate.objects.update_or_create(
                key=item["key"],
                channel=item["channel"],
                language="en",
                version=1,
                defaults={
                    **item,
                    "is_active": True,
                },
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if created else 'Updated'} template: {template}"
                )
            )
