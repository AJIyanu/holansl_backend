import logging

from django.db.models import Count, Prefetch
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from crm.models import Party
from holansladminservices.pagination import StandardPagination
from . import analytics, models as m, serializers as z, services
from .filters import ItemRequestFilter, RequestedItemFilter
from .permissions import ProcurementPermission

logger = logging.getLogger(__name__)


def item_queryset():
    return (
        m.RequestedItem.objects.select_related("request", "created_by", "completed_by")
        .prefetch_related(
            "images",
            Prefetch(
                "activities",
                queryset=m.ItemActivity.objects.order_by("-occurred_at", "-created_at"),
            ),
        )
        .annotate(activity_count=Count("activities", distinct=True))
    )


class ItemRequestViewSet(viewsets.ModelViewSet):
    queryset = m.ItemRequest.objects.select_related(
        "client", "created_by"
    ).prefetch_related(Prefetch("items", queryset=item_queryset()))
    serializer_class = z.ItemRequestSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = ItemRequestFilter
    ordering_fields = ["received_at", "client_name", "created_at"]
    ordering = ["-received_at", "-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request):
        form = z.ItemRequestInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.create_request(request.user, dict(form.validated_data))
        row = self.get_queryset().get(pk=row.pk)
        return Response(self.get_serializer(row).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="client-options")
    def client_options(self, request):
        search = request.query_params.get("search", "")[:100]
        rows = (
            Party.objects.active()
            .with_role("CLIENT")
            .filter(display_name__icontains=search)[:30]
        )
        return Response(
            [{"id": str(row.pk), "label": row.display_name} for row in rows]
        )


class RequestedItemViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = item_queryset()
    serializer_class = z.RequestedItemSerializer
    permission_classes = [ProcurementPermission]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_class = RequestedItemFilter
    ordering_fields = [
        "created_at",
        "name",
        "request__client_name",
        "deadline",
        "urgency",
        "status",
        "updated_at",
    ]
    ordering = ["-created_at"]
    action_permissions = {
        "tracker": "procurement.view_itemtracker",
        "activities": {
            "GET": "procurement.view_itemtracker",
            "POST": "procurement.add_itemactivity",
        },
        "change_status": "procurement.change_itemstatus",
        "edit": "procurement.change_requesteditem",
        "images": {
            "POST": "procurement.add_requesteditemimage",
            "DELETE": "procurement.delete_requesteditemimage",
        },
    }

    @action(detail=True, methods=["get"])
    def tracker(self, request, pk=None):
        item = self.get_object()
        data = self.get_serializer(item).data
        activities = item.activities.order_by("occurred_at", "created_at", "id")
        data["activities"] = z.ActivitySerializer(activities, many=True).data
        data["suggested_titles"] = [
            {"value": code, "label": label}
            for code, label in services.ACTIVITY_TITLES.items()
        ]
        return Response(data)

    @action(detail=True, methods=["get", "post"])
    def activities(self, request, pk=None):
        item = self.get_object()
        if request.method == "GET":
            page = self.paginate_queryset(
                item.activities.order_by("occurred_at", "created_at", "id")
            )
            return self.get_paginated_response(
                z.ActivitySerializer(page, many=True).data
            )
        form = z.ActivityInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.add_activity(request.user, item, form.validated_data)
        return Response(z.ActivitySerializer(row).data, status=201)

    @action(detail=True, methods=["patch"])
    def edit(self, request, pk=None):
        form = z.ItemEditInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.edit_item(request.user, self.get_object(), form.validated_data)
        return Response(self.get_serializer(item_queryset().get(pk=row.pk)).data)

    @action(detail=True, methods=["post"], url_path="status")
    def change_status(self, request, pk=None):
        form = z.StatusInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.change_status(
            request.user, self.get_object(), form.validated_data
        )
        return Response(self.get_serializer(item_queryset().get(pk=row.pk)).data)

    @action(
        detail=True,
        methods=["post", "delete"],
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def images(self, request, pk=None):
        item = self.get_object()
        if request.method == "DELETE":
            form = z.ImageDeleteInput(data=request.data)
            form.is_valid(raise_exception=True)
            removed = services.remove_images(
                request.user, item, form.validated_data["images"]
            )
            return Response({"removed": len(removed)})
        files = request.FILES.getlist("files")
        if not files and request.FILES.get("file"):
            files = [request.FILES["file"]]
        form = z.ImagesInput(
            data={"files": files, "caption": request.data.get("caption", "")}
        )
        form.is_valid(raise_exception=True)
        rows = services.upload_images(
            request.user,
            item,
            form.validated_data["files"],
            form.validated_data.get("caption", ""),
        )
        return Response(z.ItemImageSerializer(rows, many=True).data, status=201)


class ItemImageViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = m.RequestedItemImage.objects.select_related("item__request")
    serializer_class = z.ItemImageSerializer
    permission_classes = [ProcurementPermission]
    action_permissions = {"download": "procurement.download_itemimage"}
    http_method_names = ["get", "head", "options"]

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        row = self.get_object()
        downloaded = services.download_image(row)
        response = FileResponse(
            downloaded.file_object,
            content_type=downloaded.mime_type,
            as_attachment=True,
            filename=downloaded.filename,
        )
        response["Cache-Control"] = "private, no-store"
        return response


class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [ProcurementPermission]
    action_permissions = {
        "summary": "procurement.view_itemtracker",
        "ai_insight": "procurement.view_itemtracker",
    }

    @action(detail=False, methods=["get"])
    def summary(self, request):
        try:
            result = analytics.get_cached_analytics(
                request.query_params.get("range", "year")
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_analytics_range"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(result)

    @action(detail=False, methods=["get"], url_path="ai-insight")
    def ai_insight(self, request):
        try:
            result = analytics.get_cached_ai_analytics(
                request.query_params.get("range", "year")
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_analytics_range"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except analytics.AIConfigurationError:
            return Response(
                {
                    "detail": "AI Procurement analysis is temporarily unavailable.",
                    "code": "ai_insight_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            logger.exception("Procurement AI analytics generation failed")
            return Response(
                {
                    "detail": "AI Procurement analysis could not be generated.",
                    "code": "ai_insight_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(result)
