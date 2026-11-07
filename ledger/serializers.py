import uuid
from decimal import Decimal

from rest_framework import serializers

from .models import CashBookEntry, CashBookJournal, LedgerAccount, PRIMARY_CASH_ID


class CashBookWriteSerializer(serializers.Serializer):
    account = serializers.PrimaryKeyRelatedField(
        queryset=LedgerAccount.objects.filter(is_active=True), default=PRIMARY_CASH_ID
    )
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"], default="NGN")

    def validate(self, attrs):
        account = attrs["account"]
        if not isinstance(account, LedgerAccount):
            account = LedgerAccount.objects.get(pk=account)
            attrs["account"] = account
        if account.currency != attrs["currency"]:
            raise serializers.ValidationError("Account currency must match the entry.")
        return attrs

    date = serializers.DateField()
    kind = serializers.ChoiceField(choices=CashBookEntry.Kind.choices)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01")
    )
    description = serializers.CharField(max_length=500)
    reference = serializers.CharField(max_length=100, allow_blank=True, default="")
    request_key = serializers.UUIDField(default=uuid.uuid4)


class CashBookEditSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    kind = serializers.ChoiceField(choices=CashBookEntry.Kind.choices, required=False)
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False
    )
    description = serializers.CharField(max_length=500, required=False)
    reference = serializers.CharField(max_length=100, allow_blank=True, required=False)
    version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500)


class CashBookVoidSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=500)


class CashBookSerializer(serializers.ModelSerializer):
    party_transaction_id = serializers.UUIDField(
        source="party_transaction.id", read_only=True, allow_null=True
    )
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True
    )
    updated_by_name = serializers.CharField(
        source="updated_by.username", read_only=True
    )
    running_balance = serializers.DecimalField(
        max_digits=24, decimal_places=2, read_only=True
    )

    class Meta:
        model = CashBookEntry
        exclude = ["request_key"]
        read_only_fields = [field.name for field in CashBookEntry._meta.fields]


class CashBookJournalSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = CashBookJournal
        fields = "__all__"
