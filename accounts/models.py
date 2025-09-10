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