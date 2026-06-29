from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    TaskBatchViewSet,
    TaskReminderViewSet,
    TaskViewSet,
)

router = DefaultRouter()

# Register batches before the empty task prefix.
router.register(
    r"batches",
    TaskBatchViewSet,
    basename="task-batch",
)

router.register(
    r"",
    TaskViewSet,
    basename="task",
)

router.register(
    r"reminders",
    TaskReminderViewSet,
    basename="task-reminder",
)


urlpatterns = [
    path("", include(router.urls)),
]
