from notifications.constants import DeliveryStatus
from notifications.data import ProviderResult

from .base import NotificationProvider


class DashboardProvider(NotificationProvider):
    name = "dashboard"

    def send(self, delivery):
        return ProviderResult(status=DeliveryStatus.DELIVERED)
