from rest_framework import serializers
from .models import User, StaffProfile, Department, Role
from django.contrib.auth.models import Permission

class StaffProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffProfile
        fields = '__all__'
        read_only_fields = ('employee_id',)

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

class UserSerializer(serializers.ModelSerializer):
    """
    Updated serializer for the User model to allow assigning roles.
    """
    profile = StaffProfileSerializer(read_only=True)
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