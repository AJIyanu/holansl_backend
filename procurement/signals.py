from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ItemActivity
from .notifications import send_item_activity_notification


@receiver(
    post_save,
    sender=ItemActivity,
    dispatch_uid="procurement.notify_new_item_activity",
)
def notify_new_item_activity(sender, instance, created, **kwargs):
    if not created:
        return
    activity_id = instance.pk
    transaction.on_commit(lambda: send_item_activity_notification(activity_id))
