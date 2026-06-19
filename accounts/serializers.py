from rest_framework import serializers
from django.contrib.auth.models import Permission
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from django.contrib.auth import get_user_model

from .models import User, StaffProfile, Department, Role, AuditLog, PasswordResetCode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from .utils import (
    create_audit_log,
    create_password_reset_code,
    get_valid_password_reset_code,
    send_password_reset_email,
)


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

        if self.user.must_change_password:
            self.handle_default_password_login(request=request, username=username)
            raise ValidationError(
                {
                    "detail": "Password change required. A reset link has been sent to your email.",
                    "code": "password_change_required",
                    "reset_required": True,
                }
            )

        create_audit_log(
            user=self.user,
            event_category=AuditLog.EventCategory.AUTH,
            event_type=AuditLog.EventType.LOGIN_SUCCESS,
            status=AuditLog.EventStatus.SUCCESS,
            username_attempted=username,
            request=request,
            metadata={
                "path": request.path if request else "",
                "method": request.method if request else "",
            },
        )

        return data

    def log_failed_login(self, request, username, error):
        UserModel = get_user_model()

        user = (
            UserModel.objects.filter(username=username).first()
            or UserModel.objects.filter(email=username).first()
        )

        create_audit_log(
            user=user,
            event_category=AuditLog.EventCategory.AUTH,
            event_type=AuditLog.EventType.LOGIN_FAILED,
            status=AuditLog.EventStatus.FAILED,
            username_attempted=username,
            request=request,
            metadata={
                "error": error,
                "path": request.path if request else "",
                "method": request.method if request else "",
            },
        )

    def handle_default_password_login(self, request, username):
        reset_code, raw_token = create_password_reset_code(
            self.user,
            request=request,
            purpose=PasswordResetCode.Purpose.DEFAULT_PASSWORD_CHANGE,
        )

        try:
            email_result = send_password_reset_email(
                self.user,
                raw_token,
                purpose=PasswordResetCode.Purpose.DEFAULT_PASSWORD_CHANGE,
            )
        except Exception:
            reset_code.delete()

            create_audit_log(
                user=self.user,
                target_user=self.user,
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.PASSWORD_RESET_EMAIL_FAILED,
                status=AuditLog.EventStatus.FAILED,
                username_attempted=username,
                request=request,
                metadata={
                    "reason": "Resend failed to accept the email.",
                    "purpose": (
                        PasswordResetCode.Purpose.DEFAULT_PASSWORD_CHANGE
                    ),
                },
            )

            raise ValidationError(
                {
                    "detail": (
                        "Password change is required, but the reset email "
                        "could not be sent. Please try again."
                    ),
                    "code": "password_reset_email_failed",
                    "reset_required": True,
                }
            )

        create_audit_log(
            user=self.user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.DEFAULT_PASSWORD_LOGIN_BLOCKED,
            status=AuditLog.EventStatus.FAILED,
            username_attempted=username,
            request=request,
            metadata={
                "reason": "User must change default password before login.",
            },
        )

        create_audit_log(
            user=self.user,
            target_user=self.user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
            status=AuditLog.EventStatus.SUCCESS,
            username_attempted=username,
            request=request,
            metadata={
                "reset_code_id": str(reset_code.id),
                "expires_at": reset_code.expires_at.isoformat(),
                "email_id": email_result.get("email_id"),
                "purpose": reset_code.purpose,
            },
        )

class PasswordResetVerifySerializer(serializers.Serializer):
    code = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get("request")
        raw_code = attrs["code"]

        reset_code = get_valid_password_reset_code(raw_code)

        if not reset_code:
            create_audit_log(
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.PASSWORD_RESET_FAILED,
                status=AuditLog.EventStatus.FAILED,
                request=request,
                metadata={"reason": "Invalid or expired reset code."},
            )
            raise ValidationError(
                {
                    "detail": "Password reset link is invalid or has expired.",
                    "code": "reset_link_invalid_or_expired",
                }
            )

        if not reset_code.opened_at:
            reset_code.opened_at = timezone.now()
            reset_code.save(update_fields=["opened_at"])

        create_audit_log(
            user=reset_code.user,
            target_user=reset_code.user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.PASSWORD_RESET_CODE_VERIFIED,
            status=AuditLog.EventStatus.SUCCESS,
            request=request,
            metadata={"reset_code_id": str(reset_code.id)},
        )

        attrs["reset_code"] = reset_code
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    code = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        request = self.context.get("request")
        raw_code = attrs["code"]
        password = attrs["password"]
        password_confirm = attrs["password_confirm"]

        reset_code = get_valid_password_reset_code(raw_code)

        if not reset_code:
            create_audit_log(
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.PASSWORD_RESET_FAILED,
                status=AuditLog.EventStatus.FAILED,
                request=request,
                metadata={"reason": "Invalid or expired reset code."},
            )
            raise ValidationError(
                {
                    "detail": "Password reset link is invalid or has expired.",
                    "code": "reset_link_invalid_or_expired",
                }
            )

        if password != password_confirm:
            raise ValidationError(
                {
                    "password_confirm": "Passwords do not match.",
                    "code": "password_mismatch",
                }
            )

        if password == settings.DEFAULT_STAFF_PASSWORD:
            raise ValidationError(
                {
                    "password": "You cannot use the default password as your new password.",
                    "code": "default_password_not_allowed",
                }
            )

        validate_password(password, user=reset_code.user)

        attrs["reset_code"] = reset_code
        return attrs

    def save(self, **kwargs):
        request = self.context.get("request")
        reset_code = self.validated_data["reset_code"]
        password = self.validated_data["password"]
        user = reset_code.user

        user.set_password(password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])

        reset_code.used_at = timezone.now()
        reset_code.save(update_fields=["used_at"])

        create_audit_log(
            user=user,
            target_user=user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.PASSWORD_RESET_COMPLETED,
            status=AuditLog.EventStatus.SUCCESS,
            request=request,
            metadata={"reset_code_id": str(reset_code.id)},
        )

        return user

class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(write_only=True)

    def validate_email(self, value):
        return value.strip().lower()

    def save(self, **kwargs):
        request = self.context.get("request")
        email = self.validated_data["email"]

        UserModel = get_user_model()

        user = UserModel.objects.filter(
            email__iexact=email,
            is_active=True,
        ).first()

        # Do not disclose whether the account exists.
        if not user:
            create_audit_log(
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.PASSWORD_RESET_REQUESTED,
                status=AuditLog.EventStatus.SUCCESS,
                username_attempted=email,
                request=request,
                metadata={
                    "account_found": False,
                    "response_obscured": True,
                },
            )
            return None

        create_audit_log(
            user=user,
            target_user=user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.PASSWORD_RESET_REQUESTED,
            status=AuditLog.EventStatus.SUCCESS,
            username_attempted=email,
            request=request,
            metadata={
                "account_found": True,
                "purpose": PasswordResetCode.Purpose.PASSWORD_RESET,
            },
        )

        reset_code, raw_token = create_password_reset_code(
            user,
            request=request,
            purpose=PasswordResetCode.Purpose.PASSWORD_RESET,
        )

        try:
            email_result = send_password_reset_email(
                user,
                raw_token,
                purpose=PasswordResetCode.Purpose.PASSWORD_RESET,
            )
        except Exception:
            reset_code.delete()

            create_audit_log(
                user=user,
                target_user=user,
                event_category=AuditLog.EventCategory.SECURITY,
                event_type=AuditLog.EventType.PASSWORD_RESET_EMAIL_FAILED,
                status=AuditLog.EventStatus.FAILED,
                username_attempted=email,
                request=request,
                metadata={
                    "reason": "Resend failed to accept the email.",
                    "purpose": PasswordResetCode.Purpose.PASSWORD_RESET,
                },
            )
            raise serializers.ValidationError(
                {
                    "detail": (
                        "The password reset request could not be "
                        "processed. Please try again."
                    ),
                    "code": "password_reset_email_failed",
                }
            )

        create_audit_log(
            user=user,
            target_user=user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.PASSWORD_RESET_LINK_SENT,
            status=AuditLog.EventStatus.SUCCESS,
            username_attempted=email,
            request=request,
            metadata={
                "reset_code_id": str(reset_code.id),
                "expires_at": reset_code.expires_at.isoformat(),
                "email_id": email_result.get("email_id"),
                "purpose": reset_code.purpose,
            },
        )

        return user

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
        request = self.context.get("request")
        roles = validated_data.pop("groups", [])

        validated_data.pop("password", None)

        user = User.objects.create_user(
            **validated_data,
            password=settings.DEFAULT_STAFF_PASSWORD,
        )
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])

        user.groups.set(roles)

        create_audit_log(
            user=request.user if request and request.user.is_authenticated else None,
            target_user=user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.ACCOUNT_CREATED,
            status=AuditLog.EventStatus.SUCCESS,
            request=request,
            metadata={
                "created_user_id": str(user.id),
                "created_username": user.username,
                "created_email": user.email,
                "roles": list(user.groups.values_list("name", flat=True)),
            },
        )

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
        request = self.context.get("request")
        user_data = validated_data.pop("user", None)

        if not user_data:
            raise serializers.ValidationError({"user": "User details are required."})

        user_data.pop("password", None)

        user = User.objects.create_user(
            **user_data,
            password=settings.DEFAULT_STAFF_PASSWORD,
        )
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])

        profile = StaffProfile.objects.create(user=user, **validated_data)

        create_audit_log(
            user=request.user if request and request.user.is_authenticated else None,
            target_user=user,
            event_category=AuditLog.EventCategory.SECURITY,
            event_type=AuditLog.EventType.ACCOUNT_CREATED,
            status=AuditLog.EventStatus.SUCCESS,
            request=request,
            metadata={
                "created_user_id": str(user.id),
                "created_username": user.username,
                "created_email": user.email,
                "profile_id": str(profile.id),
                "department_id": str(profile.department_id) if profile.department_id else None,
            },
        )

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
