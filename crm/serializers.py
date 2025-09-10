from rest_framework import serializers
from .models import Party, ContactPerson


class ContactPersonSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactPerson
        fields = "__all__"


class PartySerializer(serializers.ModelSerializer):
    contact_person = ContactPersonSerializer(write_only=True, required=False)
    contacts = ContactPersonSerializer(source="contactperson_set", many=True, read_only=True)

    class Meta:
        model = Party
        fields = ["id", "name", "party_type", "is_organization", "created_at", "updated_at",
                  "contact_person", "contacts"]

    def create(self, validated_data):
        contact_person_data = validated_data.pop("contact_person", None)
        party = Party.objects.create(**validated_data)

        if contact_person_data:
            ContactPerson.objects.create(party=party, **contact_person_data)

        return party