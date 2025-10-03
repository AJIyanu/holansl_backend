from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver
from .models import SupplierQuote, ClientRequest, PurchaseOrder, POTracker


@receiver(post_save, sender=SupplierQuote)
def mark_request_completed(sender, instance, created, **kwargs):
    """When a SupplierQuote is created, mark ClientRequest as completed."""
    if instance.client_request and instance.client_request.status != "completed":
        instance.client_request.status = "completed"
        instance.client_request.save(update_fields=["status"])


@receiver(post_delete, sender=SupplierQuote)
def revert_request_status(sender, instance, **kwargs):
    """If SupplierQuote deleted and none left, revert ClientRequest to processing."""
    client_request = instance.client_request
    if client_request and not SupplierQuote.objects.filter(
            client_request=client_request).exists():
        client_request.status = "processing"
        client_request.save(update_fields=["status"])


# 🔹 Track Purchase Orders
@receiver(post_save, sender=PurchaseOrder)
def create_or_update_potracker(sender, instance, created, **kwargs):
    """
    When a new PurchaseOrder is created → create a POTracker.
    On status change → log the update in POTracker.
    """
    if created:
        POTracker.objects.create(
            purchase_order=instance,
            status=instance.status,
            description=f"Purchase Order {
                instance.po_number} created with status '{
                instance.status}'.",
        )
    else:
        # Update existing tracker or create log entry
        last_tracker = POTracker.objects.filter(
            purchase_order=instance).order_by("-updated_at").first()
        if last_tracker and last_tracker.status != instance.status:
            POTracker.objects.create(
                purchase_order=instance,
                status=instance.status,
                description=f"Status changed to '{
                    instance.status}' from {
                    last_tracker.status}.",
            )
