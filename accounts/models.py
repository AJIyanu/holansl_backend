import uuid

from django.contrib.auth.models import AbstractUser, Group
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# =============================================================================
# AUTHENTICATION MODELS
# =============================================================================


class User(AbstractUser):
    """
    Custom User model where the primary key is a UUID.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("email address"), unique=True)
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return self.username


class Role(Group):
    """
    Proxy model for Django's Group to represent a 'Role'.
    """

    class Meta:
        proxy = True
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")


# =============================================================================
# PROFILE AND DEPARTMENT MODELS
# =============================================================================


class Department(models.Model):
    """
    Represents a department within the company. Includes a short code
    for use in identifiers like employee IDs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=150, unique=True, help_text="The name of the department."
    )
    code = models.CharField(
        max_length=5,
        unique=True,
        help_text="A short code for the department (e.g., HR, FIN, PRO).",
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="A brief description of the department's function.",
    )

    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = _("Departments")
        ordering = ["name"]

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    """
    Stores detailed profile information for a staff member.
    Automatically generates a unique employee_id upon creation.
    """

    class EmploymentType(models.TextChoices):
        FULL_TIME = "FT", _("Full-Time")
        PART_TIME = "PT", _("Part-Time")
        CONTRACT = "CT", _("Contract")
        INTERN = "IN", _("Intern")

    class Sex(models.TextChoices):
        MALE = "M", _("Male")
        FEMALE = "F", _("Female")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,  # Or models.PROTECT to prevent department deletion if staff exist
        null=True,
        related_name="staff_members",
    )

    reports_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
        help_text="The staff member's immediate reporting manager.",
    )

    employee_id = models.CharField(
        max_length=20, unique=True, blank=True, editable=False
    )
    job_title = models.CharField(max_length=100)
    employment_type = models.CharField(
        max_length=2, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, unique=True)
    address = models.CharField(max_length=255, blank=True)

    middle_name = models.CharField(max_length=100, blank=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=100, blank=True, default="Nigerian")

    class Meta:
        verbose_name = _("Staff Profile")
        verbose_name_plural = _("Staff Profiles")
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self):
        return self.user.get_full_name() or self.user.username

    def clean(self):
        super().clean()

        if not self.reports_to_id:
            return

        if self.pk and self.reports_to_id == self.pk:
            raise ValidationError(
                {"reports_to": ("A staff member cannot report to themselves.")}
            )

        manager = self.reports_to

        if not manager.user.is_active:
            raise ValidationError(
                {"reports_to": ("The selected reporting manager is inactive.")}
            )

        if manager.end_date and manager.end_date < timezone.localdate():
            raise ValidationError(
                {
                    "reports_to": (
                        "The selected reporting manager's employment has ended."
                    )
                }
            )

        visited_profile_ids = set()
        current_profile = manager

        while current_profile is not None:
            if self.pk and current_profile.pk == self.pk:
                raise ValidationError(
                    {
                        "reports_to": (
                            "This reporting relationship would "
                            "create a circular hierarchy."
                        )
                    }
                )

            if current_profile.pk in visited_profile_ids:
                raise ValidationError(
                    {
                        "reports_to": (
                            "The selected reporting chain already "
                            "contains a circular relationship."
                        )
                    }
                )

            visited_profile_ids.add(current_profile.pk)
            current_profile = current_profile.reports_to

    def save(self, *args, **kwargs):
        """
        Overrides the save method to auto-generate the employee_id
        for a new staff member before saving.
        """
        if not self.employee_id and self.department:
            last_serial = StaffProfile.objects.filter(
                department=self.department
            ).count()
            next_serial = last_serial + 1

            # Format: HOL-{DEPT_CODE}-{001}
            self.employee_id = f"HOL-{self.department.code.upper()}-{next_serial:03d}"

        super().save(*args, **kwargs)


class DepartmentLeadership(models.Model):
    """
    Assigns one or more authorised leaders to a department.

    This is separate from StaffProfile.reports_to because a reporting
    manager and a department manager are not always the same person.
    """

    class LeadershipType(models.TextChoices):
        MANAGER = "MANAGER", _("Manager")
        DEPUTY = "DEPUTY", _("Deputy Manager")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="leadership_assignments",
    )

    manager = models.ForeignKey(
        StaffProfile,
        on_delete=models.PROTECT,
        related_name="department_leaderships",
    )

    leadership_type = models.CharField(
        max_length=20,
        choices=LeadershipType.choices,
        default=LeadershipType.MANAGER,
    )

    is_primary = models.BooleanField(
        default=False,
        help_text=("Identifies the principal leader for the department."),
    )

    active_from = models.DateField(
        default=timezone.localdate,
    )

    active_until = models.DateField(
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_department_leaderships",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        verbose_name = _("Department Leadership")
        verbose_name_plural = _("Department Leadership")

        ordering = [
            "department__name",
            "-is_primary",
            "manager__user__first_name",
        ]

        permissions = [
            (
                "manage_departmentleadership",
                "Can manage department leadership",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(active_until__isnull=True) | Q(active_until__gte=F("active_from"))
                ),
                name="acct_leadership_dates_valid",
            ),
            models.UniqueConstraint(
                fields=[
                    "department",
                    "manager",
                    "leadership_type",
                    "active_from",
                ],
                name="acct_unique_leadership_term",
            ),
            models.UniqueConstraint(
                fields=["department"],
                condition=Q(
                    is_primary=True,
                    active_until__isnull=True,
                ),
                name="acct_one_open_primary_lead",
            ),
        ]

    def __str__(self):
        return (
            f"{self.manager} - {self.department} ({self.get_leadership_type_display()})"
        )

    @property
    def is_active(self):
        today = timezone.localdate()

        return bool(
            self.manager.user.is_active
            and self.active_from <= today
            and (self.active_until is None or self.active_until >= today)
        )

    def clean(self):
        super().clean()

        if self.active_until and self.active_until < self.active_from:
            raise ValidationError(
                {"active_until": ("The end date cannot be before the start date.")}
            )

        if not self.manager_id:
            return

        if not self.manager.user.is_active and (
            self.active_until is None or self.active_until >= timezone.localdate()
        ):
            raise ValidationError(
                {
                    "manager": (
                        "An inactive staff member cannot be "
                        "assigned as an active department leader."
                    )
                }
            )

        if self.active_from < self.manager.start_date:
            raise ValidationError(
                {
                    "active_from": (
                        "The leadership start date cannot be "
                        "before the manager's employment start date."
                    )
                }
            )

        if self.manager.end_date and self.active_from > self.manager.end_date:
            raise ValidationError(
                {
                    "active_from": (
                        "The leadership start date cannot be after "
                        "the manager's employment end date."
                    )
                }
            )

        if self.is_primary and self.department_id:
            overlapping_primary = DepartmentLeadership.objects.filter(
                department_id=self.department_id,
                is_primary=True,
            )

            if self.pk:
                overlapping_primary = overlapping_primary.exclude(pk=self.pk)

            if self.active_until:
                overlapping_primary = overlapping_primary.filter(
                    active_from__lte=self.active_until
                )

            overlapping_primary = overlapping_primary.filter(
                Q(active_until__isnull=True) | Q(active_until__gte=self.active_from)
            )

            if overlapping_primary.exists():
                raise ValidationError(
                    {
                        "is_primary": (
                            "This department already has a "
                            "primary leader during the selected period."
                        )
                    }
                )


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
        PASSWORD_RESET_LINK_SENT = (
            "PASSWORD_RESET_LINK_SENT",
            "Password Reset Link Sent",
        )
        PASSWORD_RESET_CODE_VERIFIED = (
            "PASSWORD_RESET_CODE_VERIFIED",
            "Password Reset Code Verified",
        )
        PASSWORD_RESET_COMPLETED = (
            "PASSWORD_RESET_COMPLETED",
            "Password Reset Completed",
        )
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
            models.Index(
                fields=["target_user", "-created_at"], name="audit_target_created_idx"
            ),
            models.Index(
                fields=["event_type", "-created_at"], name="audit_event_created_idx"
            ),
            models.Index(
                fields=["event_category", "-created_at"], name="audit_cat_created_idx"
            ),
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
