from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import PartyViewSet, ContactPersonViewSet

router = DefaultRouter()
router.register(r'parties', PartyViewSet, basename="party")
router.register(r'contacts', ContactPersonViewSet, basename="contact")

urlpatterns = [
    path("", include(router.urls)),
]
