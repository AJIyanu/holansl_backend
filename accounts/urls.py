from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet,
    StaffProfileViewSet,
    DepartmentViewSet,
    RoleViewSet,
    PermissionViewSet,
    CurrentUserView,
    PasswordResetVerifyView,
    PasswordResetConfirmView,
    health_check,
)

# Create a router and register our viewsets with it.
router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'profiles', StaffProfileViewSet)
router.register(r'departments', DepartmentViewSet)
router.register(r'roles', RoleViewSet)
router.register(r'permissions', PermissionViewSet)


# The API URLs are now determined automatically by the router.
urlpatterns = [
    path("", include(router.urls)),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path(
        "password-reset/verify/",
        PasswordResetVerifyView.as_view(),
        name="password-reset-verify",
    ),
    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("health/", health_check, name="health_check"),
]