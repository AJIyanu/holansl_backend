from django.contrib.auth.models import Permission
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import (
    AllowAny,
    IsAdminUser,
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .audit_services import (
    get_cached_audit_ai_insight,
    get_cached_audit_summary,
    get_cached_login_ai_insight,
    get_cached_login_summary,
)
from .models import (
    AuditLog,
    Department,
    DepartmentLeadership,
    Role,
    StaffProfile,
    User,
)
from .permissions import (
    CanManageDepartmentLeadership,
    CanViewAuditLogs,
    IsSecurityExecutive,
    can_manage_staff_security,
    StrictDjangoModelPermissions,
)
from .serializers import (
    AuditLogSerializer,
    CurrentUserSerializer,
    DepartmentLeadershipReadSerializer,
    DepartmentLeadershipWriteSerializer,
    DepartmentSerializer,
    ForgotPasswordSerializer,
    HolanTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetVerifySerializer,
    PermissionSerializer,
    RoleSerializer,
    StaffProfileReadSerializer,
    StaffProfileWriteSerializer,
    UserSummarySerializer,
    UserWriteSerializer,
)


class StaffMutationGuard:
    """Apply privilege guards to creation, PUT and PATCH consistently."""

    def check_staff_mutation(self, serializer):
        actor = self.request.user
        if actor.is_superuser:
            return
        instance = serializer.instance
        target = instance.user if isinstance(instance, StaffProfile) else instance
        if target is not None and target.is_superuser:
            raise PermissionDenied("Only a superuser may modify a superuser account.")
        data = serializer.validated_data
        if {"is_active", "is_staff"} & data.keys() and not can_manage_staff_security(
            actor
        ):
            raise PermissionDenied(
                "Changing account status requires accounts.change_user."
            )
        roles = data.get("groups", data.get("roles"))
        if roles is not None:
            if not actor.has_perm("accounts.change_role"):
                raise PermissionDenied("Assigning roles requires accounts.change_role.")
            for role in roles:
                required = [
                    f"{app}.{code}"
                    for app, code in role.permissions.values_list(
                        "content_type__app_label", "codename"
                    )
                ]
                if not actor.has_perms(required):
                    raise PermissionDenied(
                        "You cannot assign permissions you do not hold."
                    )

    def perform_create(self, serializer):
        self.check_staff_mutation(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.check_staff_mutation(serializer)
        super().perform_update(serializer)


class UserViewSet(StaffMutationGuard, viewsets.ModelViewSet):
    """Staff-only users API with action-specific accounts user permissions.

    PUT/PATCH require change_user. Role assignment also requires change_role and
    possession of the assigned permissions. Only superusers may modify superuser
    accounts or permanently delete users. CEO/CTO role names do not bypass checks.
    """

    queryset = User.objects.prefetch_related("groups").all().order_by("-date_joined")

    permission_classes = [
        IsAdminUser,
        StrictDjangoModelPermissions,
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


class StaffProfileViewSet(StaffMutationGuard, viewsets.ModelViewSet):
    """Staff-only profiles API with action-specific staffprofile permissions.

    PUT/PATCH require change_staffprofile. Account-status changes also require
    change_user; role assignment requires change_role and the assigned permissions.
    Superuser accounts and permanent profile deletion remain superuser-protected.
    """

    queryset = (
        StaffProfile.objects.select_related(
            "user",
            "department",
            "reports_to",
            "reports_to__user",
            "reports_to__department",
        )
        .prefetch_related("user__groups")
        .all()
    )

    permission_classes = [
        IsAdminUser,
        StrictDjangoModelPermissions,
    ]

    filterset_fields = [
        "job_title",
        "employment_type",
        "department",
        "reports_to",
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
        "reports_to__employee_id",
        "reports_to__user__username",
        "reports_to__user__first_name",
        "reports_to__user__last_name",
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

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "You do not have permission to perform this operation."
            )

        return super().destroy(request, *args, **kwargs)


class DepartmentViewSet(viewsets.ModelViewSet):
    """API endpoint for departments."""

    today = timezone.localdate()

    active_leadership_queryset = (
        DepartmentLeadership.objects.select_related(
            "manager__user",
            "manager__department",
            "created_by",
        )
        .filter(active_from__lte=today)
        .filter(Q(active_until__isnull=True) | Q(active_until__gte=today))
        .order_by(
            "-is_primary",
            "manager__user__first_name",
            "manager__user__last_name",
        )
    )

    queryset = Department.objects.prefetch_related(
        Prefetch(
            "leadership_assignments",
            queryset=active_leadership_queryset,
            to_attr="active_leaderships",
        )
    )

    serializer_class = DepartmentSerializer

    permission_classes = [
        IsAdminUser,
        StrictDjangoModelPermissions,
    ]

    search_fields = ["name", "code"]
    ordering_fields = ["name"]


class DepartmentLeadershipViewSet(viewsets.ModelViewSet):
    queryset = DepartmentLeadership.objects.select_related(
        "department",
        "manager",
        "manager__user",
        "manager__department",
        "created_by",
    ).all()

    permission_classes = [
        IsAuthenticated,
        CanManageDepartmentLeadership,
    ]

    filterset_fields = [
        "department",
        "manager",
        "leadership_type",
        "is_primary",
        "active_from",
        "active_until",
    ]

    search_fields = [
        "department__name",
        "department__code",
        "manager__employee_id",
        "manager__user__username",
        "manager__user__email",
        "manager__user__first_name",
        "manager__user__last_name",
        "manager__job_title",
    ]

    ordering_fields = [
        "active_from",
        "active_until",
        "created_at",
        "updated_at",
        "department__name",
        "manager__user__first_name",
        "manager__user__last_name",
    ]

    ordering = [
        "department__name",
        "-is_primary",
        "manager__user__first_name",
    ]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve"):
            return DepartmentLeadershipReadSerializer

        return DepartmentLeadershipWriteSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied(
                "Only a superuser can permanently delete "
                "a department leadership record. Use PATCH "
                "to set active_until instead."
            )

        return super().destroy(
            request,
            *args,
            **kwargs,
        )


class RoleViewSet(viewsets.ModelViewSet):
    """Staff-only roles API requiring action-specific accounts role permissions.

    Actors cannot grant permissions they do not hold. New CEO/CTO roles receive
    all current permissions; later explicit removals are not automatically restored.
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    permission_classes = [IsAdminUser, StrictDjangoModelPermissions]

    # --- Filtering, Search, and Ordering ---
    search_fields = ["name"]
    ordering_fields = ["name"]


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint to view available permissions in the system.
    This is a read-only endpoint.
    Requires staff status and auth.view_permission (or superuser permission bypass).
    """

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [IsAdminUser, StrictDjangoModelPermissions]
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
    queryset = AuditLog.objects.select_related("user", "target_user").all()

    serializer_class = AuditLogSerializer
    permission_classes = [
        IsAuthenticated,
        CanViewAuditLogs,
    ]

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

    ordering_fields = [
        "created_at",
        "event_type",
        "status",
    ]

    @action(
        detail=False,
        methods=["get"],
        url_path="summary",
        permission_classes=[IsAuthenticated, IsSecurityExecutive],
    )
    def summary(self, request):
        try:
            summary = get_cached_audit_summary(
                request.query_params.get("range", "month")
            )
        except ValueError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": "invalid_summary_range",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(summary, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="ai-insight",
        permission_classes=[IsAuthenticated, IsSecurityExecutive],
    )
    def ai_insight(self, request):
        range_name = request.query_params.get("range", "month")

        try:
            result = get_cached_audit_ai_insight(range_name)
        except ValueError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": "invalid_summary_range",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError:
            return Response(
                {
                    "detail": "AI audit insight is temporarily unavailable.",
                    "code": "ai_insight_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            return Response(
                {
                    "detail": "AI audit insight could not be generated.",
                    "code": "ai_insight_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(result, status=status.HTTP_200_OK)


class LoginActivityViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [
        IsAuthenticated,
        CanViewAuditLogs,
    ]

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

    ordering_fields = [
        "created_at",
        "event_type",
        "status",
    ]

    def get_queryset(self):
        return AuditLog.objects.select_related("user", "target_user").filter(
            event_type__in=[
                AuditLog.EventType.LOGIN_SUCCESS,
                AuditLog.EventType.LOGIN_FAILED,
                AuditLog.EventType.LOGOUT,
                AuditLog.EventType.DEFAULT_PASSWORD_LOGIN_BLOCKED,
                AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
                AuditLog.EventType.PASSWORD_RESET_COMPLETED,
                AuditLog.EventType.PASSWORD_RESET_FAILED,
            ]
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="summary",
        permission_classes=[IsAuthenticated, IsSecurityExecutive],
    )
    def summary(self, request):
        try:
            summary = get_cached_login_summary(
                request.query_params.get("range", "month")
            )
        except ValueError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": "invalid_summary_range",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(summary, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["get"],
        url_path="ai-insight",
        permission_classes=[IsAuthenticated, IsSecurityExecutive],
    )
    def ai_insight(self, request):
        range_name = request.query_params.get("range", "month")

        try:
            result = get_cached_login_ai_insight(range_name)
        except ValueError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "code": "invalid_summary_range",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError:
            return Response(
                {
                    "detail": ("AI security insight is temporarily unavailable."),
                    "code": "ai_insight_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            # Log the real exception server-side, but do not expose
            # provider/API details to the client.
            return Response(
                {
                    "detail": ("AI security insight could not be generated."),
                    "code": "ai_insight_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(result, status=status.HTTP_200_OK)


@api_view(["GET", "HEAD"])
@permission_classes([AllowAny])
def health_check(request):
    if request.method == "HEAD":
        return HttpResponse("OK", status=200)
    return JsonResponse({"status": "ok", "message": "Server is running"})
