from django.db import models


class NotificationChannel(models.TextChoices):
    DASHBOARD = "DASHBOARD", "Dashboard"
    EMAIL = "EMAIL", "Email"
    WHATSAPP = "WHATSAPP", "WhatsApp"


class NotificationSeverity(models.TextChoices):
    INFO = "INFO", "Information"
    SUCCESS = "SUCCESS", "Success"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"
    URGENT = "URGENT", "Urgent"


class DeliveryStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    SENT = "SENT", "Sent"
    DELIVERED = "DELIVERED", "Delivered"
    READ = "READ", "Read"
    RETRYING = "RETRYING", "Retrying"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"
    CANCELLED = "CANCELLED", "Cancelled"


class NotificationEventMode(models.TextChoices):
    SHARED = "SHARED", "One event with many recipients"
    INDIVIDUAL = "INDIVIDUAL", "One event per recipient"


class NotificationProcessingMode(models.TextChoices):
    INLINE = "inline", "Inline request processing"
    OUTBOX = "outbox", "Scheduled outbox processing"
    HYBRID = "hybrid", "Inline first attempt with scheduled retries"
