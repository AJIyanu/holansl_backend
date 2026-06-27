from abc import ABC, abstractmethod


class NotificationProvider(ABC):
    """
    Base interface for every notification delivery provider.

    Email, dashboard and WhatsApp providers must implement
    the send() method.
    """

    name = "base"

    @abstractmethod
    def send(self, delivery):
        """
        Send one NotificationDelivery.

        The method must return a ProviderResult when successful,
        or raise an appropriate delivery exception when unsuccessful.
        """
        raise NotImplementedError
