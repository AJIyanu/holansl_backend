from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase


User = get_user_model()


class NotificationTestCase(APITestCase):
    def create_user(
        self,
        username,
        *,
        superuser=False,
    ):
        return User.objects.create_user(
            username=username,
            email=f"{username}@holansl.com",
            password="TestPassword123!",
            first_name=username.title(),
            is_superuser=superuser,
            is_staff=superuser,
        )
