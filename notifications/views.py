import hmac

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from django_filters.rest_framework import (
    DjangoFilterBackend,
)
from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from .constants import (
    DeliveryStatus,
    NotificationChannel,
)
from .filters import (
    NotificationDeliveryFilter,
    NotificationInboxFilter,
)
from .models import (
    NotificationDelivery,
    NotificationPreference,
    NotificationRecipient,
    NotificationTemplate,
)
from .permissions import (
    CanDispatchNotifications,
    CanManageNotificationTemplates,
    CanRetryNotificationDeliveries,
    CanViewNotificationDeliveries,
)
from .serializers import (
    NotificationDeliveryAdminSerializer,
    NotificationDispatchSerializer,
    NotificationInboxSerializer,
    NotificationPreferenceSerializer,
    NotificationTemplateSerializer,
)
from .services.delivery import (
    process_due_deliveries,
    retry_delivery,
)


@extend_schema_view(
    list=extend_schema(tags=["Notifications"]),
    retrieve=extend_schema(tags=["Notifications"]),
)
class NotificationInboxViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationInboxSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = NotificationInboxFilter

    search_fields = [
        "notification__title",
        "notification__message",
        "notification__notification_type",
        "notification__category",
    ]

    ordering_fields = [
        "created_at",
        "read_at",
        "seen_at",
        "archived_at",
    ]

    ordering = ["-created_at"]

    def get_queryset(self):
        now = timezone.now()

        return (
            NotificationRecipient.objects.select_related(
                "notification",
                "notification__actor",
                "notification__source_content_type",
                "recipient",
            )
            .prefetch_related("deliveries")
            .filter(
                recipient=self.request.user,
                deliveries__channel=(NotificationChannel.DASHBOARD),
                deliveries__status__in=[
                    DeliveryStatus.SENT,
                    DeliveryStatus.DELIVERED,
                    DeliveryStatus.READ,
                ],
            )
            .filter(
                Q(notification__scheduled_at__isnull=True)
                | Q(notification__scheduled_at__lte=now)
            )
            .filter(
                Q(notification__expires_at__isnull=True)
                | Q(notification__expires_at__gt=now)
            )
            .distinct()
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="seen",
    )
    def seen(self, request, pk=None):
        recipient = self.get_object()
        recipient.mark_seen()

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="read",
    )
    def read(self, request, pk=None):
        recipient = self.get_object()
        recipient.mark_read()

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="unread",
    )
    def unread(self, request, pk=None):
        recipient = self.get_object()

        recipient.read_at = None
        recipient.save(update_fields=["read_at"])

        recipient.deliveries.filter(channel=NotificationChannel.DASHBOARD).update(
            status=DeliveryStatus.DELIVERED,
            read_at=None,
            updated_at=timezone.now(),
        )

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="archive",
    )
    def archive(self, request, pk=None):
        recipient = self.get_object()

        if recipient.archived_at is None:
            recipient.archived_at = timezone.now()
            recipient.save(update_fields=["archived_at"])

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="restore",
    )
    def restore(self, request, pk=None):
        recipient = self.get_object()

        recipient.archived_at = None
        recipient.dismissed_at = None

        recipient.save(
            update_fields=[
                "archived_at",
                "dismissed_at",
            ]
        )

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=True,
        methods=["post"],
        url_path="dismiss",
    )
    def dismiss(self, request, pk=None):
        recipient = self.get_object()
        now = timezone.now()

        recipient.dismissed_at = now
        recipient.archived_at = recipient.archived_at or now

        recipient.save(
            update_fields=[
                "dismissed_at",
                "archived_at",
            ]
        )

        return Response(self.get_serializer(recipient).data)

    @action(
        detail=False,
        methods=["post"],
        url_path="mark-all-read",
    )
    def mark_all_read(self, request):
        now = timezone.now()

        recipient_ids = list(
            self.get_queryset()
            .filter(read_at__isnull=True)
            .values_list(
                "id",
                flat=True,
            )
        )

        updated = NotificationRecipient.objects.filter(id__in=recipient_ids).update(
            seen_at=now,
            read_at=now,
        )

        NotificationDelivery.objects.filter(
            notification_recipient_id__in=recipient_ids,
            channel=NotificationChannel.DASHBOARD,
        ).update(
            status=DeliveryStatus.READ,
            read_at=now,
            updated_at=now,
        )

        return Response({"updated": updated})

    @action(
        detail=False,
        methods=["post"],
        url_path="archive-all-read",
    )
    def archive_all_read(self, request):
        updated = (
            self.get_queryset()
            .filter(
                read_at__isnull=False,
                archived_at__isnull=True,
            )
            .update(archived_at=timezone.now())
        )

        return Response({"updated": updated})

    @action(
        detail=False,
        methods=["get"],
        url_path="unread-count",
    )
    def unread_count(self, request):
        count = (
            self.get_queryset()
            .filter(
                read_at__isnull=True,
                archived_at__isnull=True,
            )
            .count()
        )

        return Response({"count": count})


@extend_schema_view(
    list=extend_schema(tags=["Notification Preferences"]),
    retrieve=extend_schema(tags=["Notification Preferences"]),
    create=extend_schema(tags=["Notification Preferences"]),
    update=extend_schema(tags=["Notification Preferences"]),
    partial_update=extend_schema(tags=["Notification Preferences"]),
    destroy=extend_schema(tags=["Notification Preferences"]),
)
class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationPreferenceSerializer

    permission_classes = [IsAuthenticated]

    filter_backends = [filters.OrderingFilter]

    ordering_fields = [
        "category",
        "notification_type",
        "created_at",
    ]

    ordering = [
        "category",
        "notification_type",
    ]

    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema_view(
    list=extend_schema(tags=["Notification Administration"]),
    retrieve=extend_schema(tags=["Notification Administration"]),
    create=extend_schema(tags=["Notification Administration"]),
    update=extend_schema(tags=["Notification Administration"]),
    partial_update=extend_schema(tags=["Notification Administration"]),
    destroy=extend_schema(tags=["Notification Administration"]),
)
class NotificationTemplateViewSet(viewsets.ModelViewSet):
    queryset = NotificationTemplate.objects.all()

    serializer_class = NotificationTemplateSerializer

    permission_classes = [CanManageNotificationTemplates]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "key",
        "channel",
        "language",
        "is_active",
    ]

    search_fields = [
        "key",
        "name",
        "subject_template",
        "title_template",
    ]

    ordering_fields = [
        "key",
        "channel",
        "version",
        "created_at",
    ]

    ordering = [
        "key",
        "channel",
        "-version",
    ]


@extend_schema_view(
    list=extend_schema(tags=["Notification Administration"]),
    retrieve=extend_schema(tags=["Notification Administration"]),
)
class NotificationDeliveryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = NotificationDelivery.objects.select_related(
        "notification_recipient__notification",
        "notification_recipient__recipient",
    )

    serializer_class = NotificationDeliveryAdminSerializer

    permission_classes = [CanViewNotificationDeliveries]

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_class = NotificationDeliveryFilter

    search_fields = [
        "notification_recipient__notification__title",
        "notification_recipient__notification__message",
        "destination",
        "provider_message_id",
        "error_message",
    ]

    ordering_fields = [
        "created_at",
        "updated_at",
        "next_attempt_at",
        "attempt_count",
        "sent_at",
        "failed_at",
    ]

    ordering = ["-created_at"]

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[CanRetryNotificationDeliveries],
    )
    def retry(self, request, pk=None):
        delivery = self.get_object()

        retry_delivery(delivery)
        delivery.refresh_from_db()

        return Response(self.get_serializer(delivery).data)


class NotificationDispatchView(APIView):
    permission_classes = [CanDispatchNotifications]

    @extend_schema(
        tags=["Notification Administration"],
        request=NotificationDispatchSerializer,
        examples=[
            OpenApiExample(
                "Shared dashboard and email notification",
                value={
                    "recipient_ids": ["00000000-0000-0000-0000-000000000001"],
                    "notification_type": "general.message",
                    "category": "general",
                    "title": "New company update",
                    "message": "A new company update is available.",
                    "channels": [
                        "DASHBOARD",
                        "EMAIL",
                    ],
                    "event_mode": "SHARED",
                    "action_url": "/dashboard",
                    "action_label": "Open dashboard",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request):
        serializer = NotificationDispatchSerializer(
            data=request.data,
            context={"request": request},
        )

        serializer.is_valid(raise_exception=True)

        result = serializer.save()

        return Response(
            result.as_dict(),
            status=status.HTTP_201_CREATED,
        )


class ProcessNotificationDeliveriesView(APIView):
    """
    Secret-protected endpoint for external schedulers.

    This provides a fallback when the deployment does not
    have a native cron job or background worker.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Notification Internal"],
        request=None,
        responses={200: dict},
        exclude=getattr(
            settings,
            "NOTIFICATION_HIDE_INTERNAL_SCHEMA",
            True,
        ),
    )
    def post(self, request):
        configured_secret = getattr(
            settings,
            "NOTIFICATION_CRON_SECRET",
            "",
        )

        supplied_secret = request.headers.get(
            "X-Notification-Cron-Secret",
            "",
        )

        if not configured_secret:
            return Response(
                {"detail": "Notification cron processing is not configured."},
                status=(status.HTTP_503_SERVICE_UNAVAILABLE),
            )

        if not hmac.compare_digest(
            configured_secret,
            supplied_secret,
        ):
            return Response(
                {"detail": "Invalid scheduler credentials."},
                status=status.HTTP_403_FORBIDDEN,
            )

        requested_batch_size = request.query_params.get("batch_size")

        try:
            batch_size = (
                int(requested_batch_size)
                if requested_batch_size
                else (settings.NOTIFICATION_PROCESSING_BATCH_SIZE)
            )
        except (TypeError, ValueError):
            return Response(
                {"detail": "batch_size must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        batch_size = max(
            1,
            min(batch_size, 500),
        )

        return Response(process_due_deliveries(batch_size=batch_size))
