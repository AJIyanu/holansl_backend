from rest_framework import serializers

from accounts.models import (
    Department,
    User,
)


class TaskDepartmentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Department

        fields = (
            "id",
            "name",
            "code",
        )


class TaskUserSummarySerializer(serializers.ModelSerializer):
    employee_id = serializers.SerializerMethodField()
    job_title = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()

    class Meta:
        model = User

        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "employee_id",
            "job_title",
            "department",
            "is_active",
        )

    def get_employee_id(self, user):
        profile = getattr(user, "profile", None)

        return profile.employee_id if profile else None

    def get_job_title(self, user):
        profile = getattr(user, "profile", None)

        return profile.job_title if profile else None

    def get_department(self, user):
        profile = getattr(user, "profile", None)

        if not profile or not profile.department:
            return None

        return TaskDepartmentSummarySerializer(profile.department).data
