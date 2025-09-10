from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet, StaffProfileViewSet, DepartmentViewSet, RoleViewSet, PermissionViewSet

# Create a router and register our viewsets with it.
router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'profiles', StaffProfileViewSet)
router.register(r'departments', DepartmentViewSet)
router.register(r'roles', RoleViewSet)
router.register(r'permissions', PermissionViewSet)

# The API URLs are now determined automatically by the router.
urlpatterns = [
    path('', include(router.urls)),
]