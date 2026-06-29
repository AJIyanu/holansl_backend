from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class NoHardDeleteQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(
            _(
                "Task-management records cannot be "
                "permanently deleted. Use the appropriate "
                "cancel, archive or remove operation."
            )
        )


class NoHardDeleteManager(models.Manager.from_queryset(NoHardDeleteQuerySet)):
    pass
