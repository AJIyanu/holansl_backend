"""Common retained identities; posting integrity is also enforced in PostgreSQL."""

import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class RetainedQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Financial history cannot be deleted.")


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_by_name = models.CharField(max_length=255)
    objects = RetainedQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ["created_at", "id"]

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial history cannot be deleted.")


def money(**kwargs):
    return models.DecimalField(max_digits=24, decimal_places=4, **kwargs)
