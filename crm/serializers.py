from rest_framework import serializers
from .models import Party, ContactPerson


class PartySerializer(serializers.ModelSerializer):
    """
    Serializer for the Party model. Used for both displaying
    and creating new parties.
    """
    class Meta:
        model = Party
        fields = ["id", "name", "party_type", "is_organization", 'created_at', 'updated_at']


class ContactPersonSerializer(serializers.ModelSerializer):
    """
    Serializer for the ContactPerson model.
    This is the main serializer for the creation process.
    """
    party = PartySerializer(read_only=True)
    
    party_id = serializers.PrimaryKeyRelatedField(
        queryset=Party.objects.all(), source='party', write_only=True, required=False
    )
    
    new_party = PartySerializer(write_only=True, required=False)

    class Meta:
        model = ContactPerson
        fields = ["id", "first_name", "last_name", "email", "phone", "party", "party_id", "new_party"]

    def validate(self, data):
        """
        Ensure that either an existing party (party_id) or data for a
        new party (new_party) is provided, but not both.
        """
        if 'party' not in data and 'new_party' not in data:
            raise serializers.ValidationError("You must provide either 'party_id' or 'new_party'.")
        
        # if 'party' in data and 'new_party' in data:
        #     raise serializers.ValidationError("Provide either 'party_id' or 'new_party', not both.")
        
        return data

    def create(self, validated_data):
        """
        Handle the creation of the ContactPerson and the optional
        creation of a new Party.
        """
        new_party_data = validated_data.pop('new_party', None)

        if new_party_data:
            party = Party.objects.create(**new_party_data)
            validated_data['party'] = party

        contact_person = ContactPerson.objects.create(**validated_data)
        return contact_person