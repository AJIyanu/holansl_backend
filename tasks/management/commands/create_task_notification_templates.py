from django.core.management.base import BaseCommand
from django.db import transaction

from notifications.constants import (
    NotificationChannel,
)
from notifications.models import (
    NotificationTemplate,
)


TASK_TEMPLATES = [
    {
        "key": "task.assigned",
        "name": "Task assignment dashboard",
        "channel": (NotificationChannel.DASHBOARD),
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "New task assigned",
        "body_text_template": ('You have been assigned "{{ task_title }}".'),
        "body_html_template": "",
        "action_label_template": "View tasks",
    },
    {
        "key": "task.assigned",
        "name": "Task assignment email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("New task assigned: {{ task_title }}"),
        "title_template": "New task assigned",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            "{{ assigned_by_name }} assigned a new "
            "task to you.\n\n"
            "Task: {{ task_title }}\n"
            "Priority: {{ task_priority }}\n"
            "{% if task_due_at %}"
            "Due: {{ task_due_at }}\n"
            "{% endif %}\n"
            "{% if task_description %}"
            "{{ task_description }}\n\n"
            "{% endif %}"
            "{% if action_link %}"
            "View tasks: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            "<p>"
            "<strong>{{ assigned_by_name }}</strong> "
            "assigned a new task to you."
            "</p>"
            "<p>"
            "<strong>Task:</strong> "
            "{{ task_title }}<br>"
            "<strong>Priority:</strong> "
            "{{ task_priority }}"
            "{% if task_due_at %}"
            "<br><strong>Due:</strong> "
            "{{ task_due_at }}"
            "{% endif %}"
            "</p>"
            "{% if task_description %}"
            "<p>{{ task_description }}</p>"
            "{% endif %}"
            "{% if action_link %}"
            "<p>"
            '<a href="{{ action_link }}">'
            "View tasks"
            "</a>"
            "</p>"
            "{% endif %}"
        ),
        "action_label_template": "View tasks",
    },
    {
        "key": "task.assigned",
        "name": "Task assignment WhatsApp",
        "channel": (NotificationChannel.WHATSAPP),
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "New task assigned",
        "body_text_template": (
            "New task assigned\n\n"
            "{{ task_title }}\n"
            "Priority: {{ task_priority }}"
            "{% if task_due_at %}"
            "\nDue: {{ task_due_at }}"
            "{% endif %}"
            "{% if action_link %}"
            "\n\n{{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": "",
        "action_label_template": "View tasks",
    },
    {
        "key": "task.updated",
        "name": "Task update dashboard",
        "channel": NotificationChannel.DASHBOARD,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "Task details updated",
        "body_text_template": (
            '"{{ task_title }}" was updated by {{ updated_by_name }}.'
        ),
        "body_html_template": "",
        "action_label_template": "View tasks",
    },
    {
        "key": "task.updated",
        "name": "Task update email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("Task updated: {{ task_title }}"),
        "title_template": "Task details updated",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            '"{{ task_title }}" was updated by '
            "{{ updated_by_name }}.\n\n"
            "{% if action_link %}"
            "View tasks: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            '<p>"{{ task_title }}" was updated by '
            "{{ updated_by_name }}.</p>"
            "{% if action_link %}"
            '<p><a href="{{ action_link }}">'
            "View tasks</a></p>"
            "{% endif %}"
        ),
        "action_label_template": "View tasks",
    },
    {
        "key": "task.cancelled",
        "name": "Task cancellation dashboard",
        "channel": NotificationChannel.DASHBOARD,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "Task cancelled",
        "body_text_template": (
            '"{{ task_title }}" was cancelled. Reason: {{ cancellation_reason }}'
        ),
        "body_html_template": "",
        "action_label_template": "View tasks",
    },
    {
        "key": "task.cancelled",
        "name": "Task cancellation email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("Task cancelled: {{ task_title }}"),
        "title_template": "Task cancelled",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            '"{{ task_title }}" was cancelled.\n\n'
            "Reason: {{ cancellation_reason }}\n\n"
            "{% if action_link %}"
            "View tasks: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            '<p>"{{ task_title }}" was cancelled.</p>'
            "<p><strong>Reason:</strong> "
            "{{ cancellation_reason }}</p>"
            "{% if action_link %}"
            '<p><a href="{{ action_link }}">'
            "View tasks</a></p>"
            "{% endif %}"
        ),
        "action_label_template": "View tasks",
    },
    {
        "key": "task.completed",
        "name": "Task completion dashboard",
        "channel": NotificationChannel.DASHBOARD,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "Task completed",
        "body_text_template": (
            '"{{ task_title }}" was completed by {{ assignee_name }}.'
        ),
        "body_html_template": "",
        "action_label_template": "View tasks",
    },
    {
        "key": "task.completed",
        "name": "Task completion email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("Task completed: {{ task_title }}"),
        "title_template": "Task completed",
        "body_text_template": (
            '"{{ task_title }}" was completed by '
            "{{ assignee_name }}.\n\n"
            "{% if action_link %}"
            "View tasks: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            '<p>"{{ task_title }}" was completed by '
            "{{ assignee_name }}.</p>"
            "{% if action_link %}"
            '<p><a href="{{ action_link }}">'
            "View tasks</a></p>"
            "{% endif %}"
        ),
        "action_label_template": "View tasks",
    },
    {
        "key": "task.reminder",
        "name": "Task reminder dashboard",
        "channel": NotificationChannel.DASHBOARD,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "Task reminder",
        "body_text_template": (
            'Reminder for "{{ task_title }}".'
            "{% if task_due_at %} "
            "Due: {{ task_due_at }}."
            "{% endif %}"
        ),
        "body_html_template": "",
        "action_label_template": "View task",
    },
    {
        "key": "task.reminder",
        "name": "Task reminder email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("Task reminder: {{ task_title }}"),
        "title_template": "Task reminder",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            'This is your reminder for "{{ task_title }}".\n'
            "Priority: {{ task_priority }}\n"
            "{% if task_due_at %}"
            "Due: {{ task_due_at }}\n"
            "{% endif %}\n"
            "{% if task_description %}"
            "{{ task_description }}\n\n"
            "{% endif %}"
            "{% if action_link %}"
            "View task: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            "<p>This is your reminder for "
            '"{{ task_title }}".</p>'
            "<p>"
            "<strong>Priority:</strong> "
            "{{ task_priority }}"
            "{% if task_due_at %}"
            "<br><strong>Due:</strong> "
            "{{ task_due_at }}"
            "{% endif %}"
            "</p>"
            "{% if task_description %}"
            "<p>{{ task_description }}</p>"
            "{% endif %}"
            "{% if action_link %}"
            '<p><a href="{{ action_link }}">'
            "View task</a></p>"
            "{% endif %}"
        ),
        "action_label_template": "View task",
    },
    {
        "key": "task.reminder",
        "name": "Task reminder WhatsApp",
        "channel": NotificationChannel.WHATSAPP,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "Task reminder",
        "body_text_template": (
            "Task reminder\n\n"
            "{{ task_title }}\n"
            "Priority: {{ task_priority }}"
            "{% if task_due_at %}"
            "\nDue: {{ task_due_at }}"
            "{% endif %}"
            "{% if action_link %}"
            "\n\n{{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": "",
        "action_label_template": "View task",
    },
    {
        "key": "task.comment_added",
        "name": "Task comment dashboard",
        "channel": NotificationChannel.DASHBOARD,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "New task comment",
        "body_text_template": (
            '{{ comment_author_name }} commented on "{{ task_title }}".'
        ),
        "body_html_template": "",
        "action_label_template": "View task",
    },
    {
        "key": "task.comment_added",
        "name": "Task comment email",
        "channel": NotificationChannel.EMAIL,
        "language": "en",
        "version": 1,
        "subject_template": ("New comment: {{ task_title }}"),
        "title_template": "New task comment",
        "body_text_template": (
            "Hello {{ recipient_name }},\n\n"
            "{{ comment_author_name }} commented on "
            '"{{ task_title }}".\n\n'
            "{{ comment_body }}\n\n"
            "{% if action_link %}"
            "View task: {{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": (
            "<p>Hello {{ recipient_name }},</p>"
            "<p>{{ comment_author_name }} commented on "
            '"{{ task_title }}".</p>'
            "<blockquote>{{ comment_body }}</blockquote>"
            "{% if action_link %}"
            '<p><a href="{{ action_link }}">'
            "View task</a></p>"
            "{% endif %}"
        ),
        "action_label_template": "View task",
    },
    {
        "key": "task.comment_added",
        "name": "Task comment WhatsApp",
        "channel": NotificationChannel.WHATSAPP,
        "language": "en",
        "version": 1,
        "subject_template": "",
        "title_template": "New task comment",
        "body_text_template": (
            "{{ comment_author_name }} commented on "
            '"{{ task_title }}".\n\n'
            "{{ comment_body }}"
            "{% if action_link %}"
            "\n\n{{ action_link }}"
            "{% endif %}"
        ),
        "body_html_template": "",
        "action_label_template": "View task",
    },
]


class Command(BaseCommand):
    help = "Creates or updates the default task notification templates."

    @transaction.atomic
    def handle(self, *args, **options):
        for item in TASK_TEMPLATES:
            NotificationTemplate.objects.filter(
                key=item["key"],
                channel=item["channel"],
                language=item["language"],
                is_active=True,
            ).exclude(version=item["version"]).update(is_active=False)

            template, created = NotificationTemplate.objects.update_or_create(
                key=item["key"],
                channel=item["channel"],
                language=item["language"],
                version=item["version"],
                defaults={
                    **item,
                    "is_active": True,
                },
            )

            action = "Created" if created else "Updated"

            self.stdout.write(self.style.SUCCESS(f"{action}: {template}"))
