from django.db.models import Count, Prefetch
from django.http import FileResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from crm.models import Party
from holansladminservices.pagination import StandardPagination
from . import models as m, serializers as z, services
from .filters import ItemRequestFilter, RequestedItemFilter
from .permissions import ProcurementPermission


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
        "client", "requester", "created_by"
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

    @action(detail=False, methods=["get"], url_path="requester-options")
    def requester_options(self, request):
        client = request.query_params.get("client")
        rows = Party.objects.filter(
            organisation_affiliations__organisation_id=client,
            organisation_affiliations__is_current=True,
        ).distinct()[:30]
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
        "images": "procurement.add_requesteditemimage",
    }

    @action(detail=True, methods=["get"])
    def tracker(self, request, pk=None):
        item = self.get_object()
        data = self.get_serializer(item).data
        data["activities"] = z.ActivitySerializer(item.activities.all(), many=True).data
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
                item.activities.order_by("-occurred_at", "-created_at")
            )
            return self.get_paginated_response(
                z.ActivitySerializer(page, many=True).data
            )
        form = z.ActivityInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.add_activity(request.user, item, form.validated_data)
        return Response(z.ActivitySerializer(row).data, status=201)

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
        methods=["post"],
        parser_classes=[MultiPartParser, FormParser],
    )
    def images(self, request, pk=None):
        form = z.ImageInput(data=request.data)
        form.is_valid(raise_exception=True)
        row = services.upload_image(
            request.user,
            self.get_object(),
            form.validated_data["file"],
            form.validated_data.get("caption", ""),
        )
        return Response(z.ItemImageSerializer(row).data, status=201)


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
