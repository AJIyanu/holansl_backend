from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import (
    NotificationDeliveryViewSet,
    NotificationDispatchView,
    NotificationInboxViewSet,
    NotificationPreferenceViewSet,
    NotificationTemplateViewSet,
    ProcessNotificationDeliveriesView,
)


router = DefaultRouter()

router.register(
    r"preferences",
    NotificationPreferenceViewSet,
    basename="notification-preference",
)

router.register(
    r"templates",
    NotificationTemplateViewSet,
    basename="notification-template",
)

router.register(
    r"deliveries",
    NotificationDeliveryViewSet,
    basename="notification-delivery",
)

# Keep the empty prefix last so named routes above
# are matched before /notifications/<uuid>/.
router.register(
    r"",
    NotificationInboxViewSet,
    basename="notification-inbox",
)


urlpatterns = [
    path(
        "dispatch/",
        NotificationDispatchView.as_view(),
        name="notification-dispatch",
    ),
    path(
        "internal/process-deliveries/",
        ProcessNotificationDeliveriesView.as_view(),
        name="notification-process-deliveries",
    ),
    path("", include(router.urls)),
]
