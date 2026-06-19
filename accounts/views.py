from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth.models import Permission
from .models import User, StaffProfile, Department, Role
from rest_framework.permissions import DjangoModelPermissions
from .serializers import (
    UserSerializer, StaffProfileSerializer, DepartmentSerializer, RoleSerializer,
    PermissionSerializer, CurrentUserSerializer, HolanTokenObtainPairSerializer,
    PasswordResetVerifySerializer, PasswordResetConfirmSerializer, ForgotPasswordSerializer
)
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from django.http import HttpResponse

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

class UserViewSet(viewsets.ModelViewSet):
    """API endpoint that allows users to be viewed or edited."""
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser, DjangoModelPermissions]
    
    # --- Filtering, Search, and Ordering ---
    filterset_fields = ['email', 'first_name', 'last_name', 'is_staff']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering_fields = ['date_joined', 'username', 'email']

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
    """API endpoint for staff profiles."""
    queryset = StaffProfile.objects.all()
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAdminUser, DjangoModelPermissions]

    # --- Filtering, Search, and Ordering ---
    filterset_fields = ['job_title', 'employment_type', 'department']
    search_fields = ['job_title', 'phone_number', 'user__username', 'user__first_name']
    ordering_fields = ['start_date', 'job_title']

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


@api_view(['GET', 'HEAD'])
@permission_classes([AllowAny])
def health_check(request):
    if request.method == "HEAD":
        return HttpResponse("OK", status=200)
    return JsonResponse({
        'status': 'ok',
        'message': 'Server is running'
    })