from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, AllowAny
from django.contrib.auth.models import Permission
from .models import User, StaffProfile, Department, Role
from rest_framework.permissions import DjangoModelPermissions
from .serializers import (
    UserSerializer, StaffProfileSerializer, DepartmentSerializer, RoleSerializer,
    PermissionSerializer
)
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated

from rest_framework.decorators import api_view, permission_classes
from django.http import JsonResponse
from django.http import HttpResponse

class UserViewSet(viewsets.ModelViewSet):
    """API endpoint that allows users to be viewed or edited."""
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = UserSerializer
    permission_classes = [IsAdminUser, DjangoModelPermissions]
    
    # --- Filtering, Search, and Ordering ---
    filterset_fields = ['email', 'first_name', 'last_name', 'is_staff']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering_fields = ['date_joined', 'username', 'email']

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
    serializer_class = UserSerializer
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