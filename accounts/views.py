from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth.models import Permission
from .models import User, StaffProfile, Department, Role
from rest_framework.permissions import DjangoModelPermissions
from .serializers import (
    UserSerializer, DepartmentSerializer, RoleSerializer, UserWriteSerializer,
    PermissionSerializer, CurrentUserSerializer, HolanTokenObtainPairSerializer,
    PasswordResetVerifySerializer, PasswordResetConfirmSerializer, ForgotPasswordSerializer,
    AuditLogSerializer, StaffProfileReadSerializer, StaffProfileWriteSerializer, UserSummarySerializer,
)
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from django.http import HttpResponse

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import clone_request

from .models import AuditLog
from .permissions import (
    CanViewAuditLogs,
    can_manage_staff_security,
)
from .serializers import (
    AuditLogSerializer,
    StaffProfileReadSerializer,
    StaffProfileWriteSerializer,
    UserSummarySerializer,
    UserWriteSerializer,
)

class UserViewSet(viewsets.ModelViewSet):
    queryset = (
        User.objects
        .prefetch_related("groups")
        .all()
        .order_by("-date_joined")
    )

    permission_classes = [
        IsAdminUser,
        DjangoModelPermissions,
    ]

    filterset_fields = [
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
        "groups",
    ]

    search_fields = [
        "username",
        "email",
        "first_name",
        "last_name",
        "groups__name",
    ]

    ordering_fields = [
        "date_joined",
        "username",
        "email",
        "is_active",
        "is_staff",
    ]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve"):
            return UserSummarySerializer

        return UserWriteSerializer

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        privileged_fields = {
            "is_active",
            "is_staff",
            "roles",
        }

        requested_fields = set(request.data.keys())

        if (
            requested_fields & privileged_fields
            and not can_manage_staff_security(request.user)
        ):
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().destroy(request, *args, **kwargs)

class HolanTokenObtainPairView(TokenObtainPairView):
    serializer_class = HolanTokenObtainPairSerializer

class PasswordResetVerifyView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetVerifySerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        reset_code = serializer.validated_data["reset_code"]
        user = reset_code.user

        return Response(
            {
                "valid": True,
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                },
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "detail": "Password reset successful. You can now login.",
                "code": "password_reset_completed",
            },
            status=status.HTTP_200_OK,
        )

class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ForgotPasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "detail": (
                    "If an active account exists for that email address, "
                    "a password reset link has been sent."
                ),
                "code": "password_reset_requested",
            },
            status=status.HTTP_200_OK,
        )

class StaffProfileViewSet(viewsets.ModelViewSet):
    queryset = (
        StaffProfile.objects
        .select_related("user", "department")
        .prefetch_related("user__groups")
        .all()
    )

    permission_classes = [
        IsAdminUser,
        DjangoModelPermissions,
    ]

    filterset_fields = [
        "job_title",
        "employment_type",
        "department",
        "user__is_active",
        "user__is_staff",
        "user__groups",
    ]

    search_fields = [
        "employee_id",
        "job_title",
        "phone_number",
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
        "department__name",
        "user__groups__name",
    ]

    ordering_fields = [
        "start_date",
        "job_title",
        "employee_id",
        "user__first_name",
        "user__last_name",
    ]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve"):
            return StaffProfileReadSerializer

        return StaffProfileWriteSerializer

    def update(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        privileged_fields = {
            "department",
            "is_active",
            "is_staff",
            "roles",
        }

        requested_fields = set(request.data.keys())

        if (
            requested_fields & privileged_fields
            and not can_manage_staff_security(request.user)
        ):
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().destroy(request, *args, **kwargs)

class DepartmentViewSet(viewsets.ModelViewSet):
    """API endpoint for departments."""
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminUser, DjangoModelPermissions]

    # --- Filtering, Search, and Ordering ---
    search_fields = ['name', 'code']
    ordering_fields = ['name']

class RoleViewSet(viewsets.ModelViewSet):
    """API endpoint for roles (groups)."""
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAdminUser]

    # --- Filtering, Search, and Ordering ---
    search_fields = ['name']
    ordering_fields = ['name']

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint to view available permissions in the system.
    This is a read-only endpoint.
    """
    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAdminUser]
    pagination_class = None

class CurrentUserView(RetrieveAPIView):
    """
    API endpoint to retrieve the currently authenticated user's data,
    including their linked staff profile.
    """
    serializer_class = CurrentUserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        AuditLog.objects
        .select_related("user", "target_user")
        .all()
    )
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, CanViewAuditLogs]

    filterset_fields = [
        "user",
        "target_user",
        "event_category",
        "event_type",
        "status",
        "app_label",
        "resource",
        "action",
    ]

    search_fields = [
        "user__username",
        "user__email",
        "target_user__username",
        "target_user__email",
        "username_attempted",
        "object_id",
    ]

    ordering_fields = ["created_at", "event_type", "status"]


class LoginActivityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, CanViewAuditLogs]

    filterset_fields = [
        "user",
        "status",
        "event_type",
    ]

    search_fields = [
        "user__username",
        "user__email",
        "username_attempted",
        "ip_address",
    ]

    ordering_fields = ["created_at", "event_type", "status"]

    def get_queryset(self):
        return (
            AuditLog.objects
            .select_related("user", "target_user")
            .filter(
                event_type__in=[
                    AuditLog.EventType.LOGIN_SUCCESS,
                    AuditLog.EventType.LOGIN_FAILED,
                    AuditLog.EventType.LOGOUT,
                    AuditLog.EventType.DEFAULT_PASSWORD_LOGIN_BLOCKED,
                ]
            )
        )


@api_view(['GET', 'HEAD'])
@permission_classes([AllowAny])
def health_check(request):
    if request.method == "HEAD":
        return HttpResponse("OK", status=200)
    return JsonResponse({
        'status': 'ok',
        'message': 'Server is running'
    })