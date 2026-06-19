import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser, Group
from django.utils.translation import gettext_lazy as _

# =============================================================================
# AUTHENTICATION MODELS
# =============================================================================

class User(AbstractUser):
    """
    Custom User model where the primary key is a UUID.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_('email address'), unique=True)
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return self.username

class Role(Group):
    """
    Proxy model for Django's Group to represent a 'Role'.
    """
    class Meta:
        proxy = True
        verbose_name = _('Role')
        verbose_name_plural = _('Roles')

# =============================================================================
# PROFILE AND DEPARTMENT MODELS
# =============================================================================

class Department(models.Model):
    """
    Represents a department within the company. Includes a short code
    for use in identifiers like employee IDs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=150, unique=True, help_text="The name of the department.")
    code = models.CharField(
        max_length=5,
        unique=True,
        help_text="A short code for the department (e.g., HR, FIN, PRO)."
    )
    description = models.TextField(blank=True, null=True, help_text="A brief description of the department's function.")

    class Meta:
        verbose_name = _('Department')
        verbose_name_plural = _('Departments')
        ordering = ['name']

    def __str__(self):
        return self.name

class StaffProfile(models.Model):
    """
    Stores detailed profile information for a staff member.
    Automatically generates a unique employee_id upon creation.
    """
    class EmploymentType(models.TextChoices):
        FULL_TIME = 'FT', _('Full-Time')
        PART_TIME = 'PT', _('Part-Time')
        CONTRACT = 'CT', _('Contract')
        INTERN = 'IN', _('Intern')

    class Sex(models.TextChoices):
        MALE = 'M', _('Male')
        FEMALE = 'F', _('Female')
        
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL, # Or models.PROTECT to prevent department deletion if staff exist
        null=True,
        related_name='staff_members'
    )
    
    employee_id = models.CharField(max_length=20, unique=True, blank=True, editable=False)
    job_title = models.CharField(max_length=100)
    employment_type = models.CharField(max_length=2, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, unique=True)
    address = models.CharField(max_length=255, blank=True)

    middle_name = models.CharField(max_length=100, blank=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=100, blank=True, default='Nigerian')

    class Meta:
        verbose_name = _('Staff Profile')
        verbose_name_plural = _('Staff Profiles')
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return self.user.get_full_name() or self.user.username

    def save(self, *args, **kwargs):
        """
        Overrides the save method to auto-generate the employee_id
        for a new staff member before saving.
        """
        if not self.employee_id and self.department:
            last_serial = StaffProfile.objects.filter(department=self.department).count()
            next_serial = last_serial + 1
            
            # Format: HOL-{DEPT_CODE}-{001}
            self.employee_id = f"HOL-{self.department.code.upper()}-{next_serial:03d}"
            
        super().save(*args, **kwargs)

# =============================================================================
# AUTH LOGGING MODELS
# =============================================================================

class AuditLog(models.Model):
    class EventCategory(models.TextChoices):
        AUTH = "AUTH", "Authentication"
        CRUD = "CRUD", "CRUD Activity"
        SYSTEM = "SYSTEM", "System"
        SECURITY = "SECURITY", "Security"

    class EventType(models.TextChoices):
        LOGIN_SUCCESS = "LOGIN_SUCCESS", "Login Success"
        LOGIN_FAILED = "LOGIN_FAILED", "Login Failed"
        LOGOUT = "LOGOUT", "Logout"

        ACCOUNT_CREATED = "ACCOUNT_CREATED", "Account Created"
        DEFAULT_PASSWORD_LOGIN_BLOCKED = (
            "DEFAULT_PASSWORD_LOGIN_BLOCKED",
            "Default Password Login Blocked",
        )
        PASSWORD_RESET_LINK_SENT = "PASSWORD_RESET_LINK_SENT", "Password Reset Link Sent"
        PASSWORD_RESET_CODE_VERIFIED = (
            "PASSWORD_RESET_CODE_VERIFIED",
            "Password Reset Code Verified",
        )
        PASSWORD_RESET_COMPLETED = "PASSWORD_RESET_COMPLETED", "Password Reset Completed"
        PASSWORD_RESET_FAILED = "PASSWORD_RESET_FAILED", "Password Reset Failed"
        PASSWORD_RESET_REQUESTED = (
            "PASSWORD_RESET_REQUESTED",
            "Password Reset Requested",
        )
        PASSWORD_RESET_EMAIL_FAILED = (
            "PASSWORD_RESET_EMAIL_FAILED",
            "Password Reset Email Failed",
        )

        CREATE = "CREATE", "Create"
        READ = "READ", "Read"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"

    class EventStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        help_text="The user who performed the action.",
    )

    target_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_audit_logs",
        help_text="The user affected by the action, if applicable.",
    )

    event_category = models.CharField(
        max_length=20,
        choices=EventCategory.choices,
        default=EventCategory.AUTH,
    )

    event_type = models.CharField(
        max_length=80,
        choices=EventType.choices,
    )

    status = models.CharField(
        max_length=20,
        choices=EventStatus.choices,
    )

    username_attempted = models.CharField(
        max_length=255,
        blank=True,
        help_text="Username/email submitted during login attempt.",
    )

    app_label = models.CharField(max_length=100, blank=True)
    resource = models.CharField(max_length=100, blank=True)
    action = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=100, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="audit_user_created_idx"),
            models.Index(fields=["target_user", "-created_at"], name="audit_target_created_idx"),
            models.Index(fields=["event_type", "-created_at"], name="audit_event_created_idx"),
            models.Index(fields=["event_category", "-created_at"], name="audit_cat_created_idx"),
        ]

    def __str__(self):
        actor = self.user.username if self.user else self.username_attempted or "System"
        return f"{self.event_type} - {actor} - {self.created_at}"

class PasswordResetCode(models.Model):
    class Purpose(models.TextChoices):
        DEFAULT_PASSWORD_CHANGE = (
            "DEFAULT_PASSWORD_CHANGE",
            "Default Password Change",
        )
        PASSWORD_RESET = "PASSWORD_RESET", "Password Reset"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_codes",
    )

    token_hash = models.CharField(max_length=128, unique=True)

    purpose = models.CharField(
        max_length=50,
        choices=Purpose.choices,
        default=Purpose.DEFAULT_PASSWORD_CHANGE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    opened_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="reset_user_created_idx"),
            models.Index(fields=["token_hash"], name="reset_token_hash_idx"),
            models.Index(fields=["expires_at"], name="reset_expires_idx"),
        ]

    def __str__(self):
        return f"{self.user} - {self.purpose} - {self.created_at}"

    @property
    def is_used(self):
        return self.used_at is not None