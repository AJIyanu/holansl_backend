from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import Party, ContactPerson
from .serializers import PartySerializer, ContactPersonSerializer
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.permissions import DjangoModelPermissions


class PartyViewSet(viewsets.ModelViewSet):
    queryset = Party.objects.all().order_by("-created_at")
    serializer_class = PartySerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    filterset_fields = ["party_type", "is_organization"]
    search_fields = ["name", "email", "phone"]
    ordering_fields = ["name", "created_at"]


class ContactPersonViewSet(viewsets.ModelViewSet):
    queryset = ContactPerson.objects.all().order_by("first_name")
    serializer_class = ContactPersonSerializer
    permission_classes = [DjangoModelPermissions]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    filterset_fields = ["party__party_type"]
    search_fields = ["first_name", "last_name", "email", "phone"]
    ordering_fields = ["first_name", "last_name"]

