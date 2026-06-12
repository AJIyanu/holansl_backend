from rest_framework import serializers
from django.contrib.auth.models import Permission
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth import get_user_model

from .models import User, StaffProfile, Department, Role, AuditLog


DJANGO_ACTION_MAP = {
    "add": "create",
    "change": "edit",
    "delete": "delete",
    "view": "view",
}

def format_permission(permission: Permission) -> str:
    """
    Convert Django permission format:
    crm.change_party

    Into frontend format:
    crm.party.edit
    """
    app_label = permission.content_type.app_label
    codename = permission.codename

    try:
        django_action, resource = codename.split("_", 1)
    except ValueError:
        return f"{app_label}.{codename}"

    action = DJANGO_ACTION_MAP.get(django_action, django_action)

    return f"{app_label}.{resource}.{action}"

def get_client_ip(request):
    if not request:
        return None

    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def get_user_agent(request):
    if not request:
        return ""

    return request.META.get("HTTP_USER_AGENT", "")


class HolanTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        request = self.context.get("request")
        username = attrs.get(self.username_field, "")

        try:
            data = super().validate(attrs)
        except AuthenticationFailed as exc:
            self.log_failed_login(request=request, username=username, error=str(exc))
            raise exc

        self.log_successful_login(request=request, user=self.user, username=username)

        return data

    def log_successful_login(self, request, user, username):
        AuditLog.objects.create(
            user=user,
            event_category=AuditLog.EventCategory.AUTH,
            event_type=AuditLog.EventType.LOGIN_SUCCESS,
            status=AuditLog.EventStatus.SUCCESS,
            username_attempted=username,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            metadata={
                "path": request.path if request else "",
                "method": request.method if request else "",
            },
        )

    def log_failed_login(self, request, username, error):
        User = get_user_model()

        user = (
            User.objects.filter(username=username).first()
            or User.objects.filter(email=username).first()
        )

        AuditLog.objects.create(
            user=user,
            event_category=AuditLog.EventCategory.AUTH,
            event_type=AuditLog.EventType.LOGIN_FAILED,
            status=AuditLog.EventStatus.FAILED,
            username_attempted=username,
            ip_address=get_client_ip(request),
            user_agent=get_user_agent(request),
            metadata={
                "error": error,
                "path": request.path if request else "",
                "method": request.method if request else "",
            },
        )

class UserSerializer(serializers.ModelSerializer):
    """
    Updated serializer for the User model to allow assigning roles.
    """
    # profile = StaffProfileSerializer(read_only=True)
    roles = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Role.objects.all(),
        source='groups' 
    )

    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name', 
            'password', 'profile', 'roles'
        )
        extra_kwargs = {
            'password': {'write_only': True, 'style': {'input_type': 'password'}}
        }
    
    def create(self, validated_data):
        roles = validated_data.pop('groups')
        user = User.objects.create_user(**validated_data)
        user.groups.set(roles)
        return user
    
    def update(self, instance, validated_data):
        """
        Custom update method to handle password hashing.
        """
        if 'password' in validated_data:
            password = validated_data.pop('password')
            instance.set_password(password)
        
        return super().update(instance, validated_data)
    
class UserCreateNestedSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name')
        extra_kwargs = {
            'password': {'write_only': True, 'style': {'input_type': 'password'}}
        }

class StaffProfileSerializer(serializers.ModelSerializer):
    user = UserCreateNestedSerializer(write_only=True)
    user_details = UserSerializer(source='user', read_only=True)
    class Meta:
        model = StaffProfile
        fields = '__all__'
        read_only_fields = ('employee_id',)

    def create(self, validated_data):
        user_data = validated_data.pop('user', None)
        if user_data:     
            user = User.objects.create_user(**user_data)
        profile = StaffProfile.objects.create(user=user, **validated_data)
        return profile

UserSerializer.profile = StaffProfileSerializer(read_only=True, source='profile')

class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = '__all__'

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ('id', 'name', 'codename')

class RoleSerializer(serializers.ModelSerializer):
    """
    Updated serializer for the Role model to allow assigning permissions.
    """
    permissions = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Permission.objects.all()
    )
    
    class Meta:
        model = Role
        fields = ('id', 'name', 'permissions')

class CurrentUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    profile = StaffProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "is_superuser",
            "roles",
            "permissions",
            "profile",
        )

    def get_roles(self, obj):
        return list(obj.groups.values_list("name", flat=True))

    def get_permissions(self, obj):
        """
        Return user permissions in frontend format:
        app.resource.action

        Includes both direct user permissions and group/role permissions.
        """
        if not obj.is_active:
            return []

        if obj.is_superuser:
            permissions = Permission.objects.select_related("content_type").all()
        else:
            permission_strings = obj.get_all_permissions()
            codenames_by_app = {}

            for perm in permission_strings:
                app_label, codename = perm.split(".", 1)
                codenames_by_app.setdefault(app_label, set()).add(codename)

            permissions = Permission.objects.select_related("content_type").filter(
                content_type__app_label__in=codenames_by_app.keys()
            )

            permissions = [
                permission
                for permission in permissions
                if permission.codename
                in codenames_by_app.get(permission.content_type.app_label, set())
            ]

        return sorted(format_permission(permission) for permission in permissions)
