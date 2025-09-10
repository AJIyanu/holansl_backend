from rest_framework import serializers
from .models import Category, Transaction, Expectation


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class TransactionSimpleSerializer(serializers.ModelSerializer):
    """A lightweight serializer for nested transaction listing under Expectation"""

    class Meta:
        model = Transaction
        fields = ["id", "name", "amount", "currency", "status", "date"]


class ExpectationSerializer(serializers.ModelSerializer):
    transactions = TransactionSimpleSerializer(many=True, read_only=True)
    party_name = serializers.CharField(source="party.name", read_only=True)

    class Meta:
        model = Expectation
        fields = "__all__"


class TransactionSerializer(serializers.ModelSerializer):
    # category = CategorySerializer(read_only=True)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        # write_only=True,
        required=False,
        allow_null=True
    )
    # expectations = ExpectationSerializer(read_only=True)
    expectation = serializers.PrimaryKeyRelatedField(
        queryset=Expectation.objects.all(),
        # write_only=True,
        required=False,
        allow_null=True
    )

    class Meta:
        model = Transaction
        fields = "__all__"