from decimal import Decimal
import uuid
from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import Category, Transaction, Expectation
from .serializers import CategorySerializer, TransactionSerializer, ExpectationSerializer
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.utils.dateparse import parse_date

from rest_framework.permissions import DjangoModelPermissions


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["flow", "name"]
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "updated_at"]
    permission_classes = [DjangoModelPermissions]


class ExpectationViewSet(viewsets.ModelViewSet):
    queryset = Expectation.objects.all()
    serializer_class = ExpectationSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["type", "status", "currency"]
    search_fields = ["type"]
    ordering_fields = ["created_at", "updated_at", "amount"]
    permission_classes = [DjangoModelPermissions]


class TransactionViewSet(viewsets.ModelViewSet):
    queryset = Transaction.objects.all()
    serializer_class = TransactionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["status", "currency", "category", "party", "purchase_order", "supplier_offer"]
    search_fields = ["name", "description"]
    ordering_fields = ["date", "amount", "created_at", "updated_at"]
    permission_classes = [DjangoModelPermissions]

    @action(detail=False, methods=["get"])
    def balance(self, request):
        """Compute current balance: credits - debits"""
        qs = Transaction.objects.all()
        credits = Decimal("0.00")
        debits = Decimal("0.00")
        for tx in qs:
            if tx.category and tx.category.flow == "CREDIT":
                credits += tx.amount
            elif tx.category and tx.category.flow == "DEBIT":
                debits += tx.amount
        # print(credits, debits)
        # print(tx.category)
        # credits = totals.get("credits") or 0
        # debits = totals.get("debits") or 0
        balance = credits - debits

        return Response({
            "credits": credits,
            "debits": debits,
            "balance": balance
        })


class ReconciliationView(APIView):
    """
    Returns reconciliation for a party:
    - Expectations (invoice/loan) from a party
    - Transactions (payments in/out) with a party
    - Balance = expectations - transactions (tx logic depends on category)
    Supports ?party=<uuid>&start=YYYY-MM-DD&end=YYYY-MM-DD
    """
    permission_classes = [DjangoModelPermissions]

    def get(self, request):
        party_id = request.query_params.get("party")
        if not party_id:
            return Response(
                {"error": "party query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # --- validate UUID format ---
        try:
            uuid.UUID(str(party_id))
        except ValueError:
            return Response(
                {"error": "Invalid party ID format (must be UUID)."},
                status=status.HTTP_400_BAD_REQUEST
            )

        start_date = parse_date(request.query_params.get("start")) if request.query_params.get("start") else None
        end_date = parse_date(request.query_params.get("end")) if request.query_params.get("end") else None

        # --- Compute prior balance if start is provided ---
        prior_balance = None
        if start_date:
            prior_expectations = Expectation.objects.filter(
                party_id=party_id, created_at__lt=start_date
            )
            prior_transactions = Transaction.objects.filter(
                party_id=party_id, date__lt=start_date
            )
            exp_total = Decimal("0.00")
            for exp in prior_expectations:
                exp_total += exp.compound_amount if exp.compound_amount else exp.amount

            tx_total = Decimal("0.00")
            for tx in prior_transactions:
                if tx.category and tx.category.flow == "CREDIT":
                    tx_total += tx.amount
                elif tx.category and tx.category.flow == "DEBIT":
                    tx_total -= tx.amount
            prior_balance = exp_total - tx_total

        # --- Main queryset ---
        expectations = Expectation.objects.filter(party_id=party_id)
        transactions = Transaction.objects.filter(party_id=party_id)

        if end_date:
            expectations = expectations.filter(created_at__lte=end_date)
            transactions = transactions.filter(date__lte=end_date)

        # --- Compute totals ---
        exp_total = Decimal("0.00")
        for exp in expectations:
            exp_total += exp.compound_amount if exp.compound_amount else exp.amount

        tx_total = Decimal("0.00")
        for tx in transactions:
            if tx.category and tx.category.flow == "CREDIT":
                tx_total += tx.amount
            elif tx.category and tx.category.flow == "DEBIT":
                tx_total -= tx.amount

        balance = exp_total - tx_total

        if start_date:
            expectations = expectations.filter(created_at__gte=start_date)
            transactions = transactions.filter(date__gte=start_date)

        return Response({
            "party": party_id,
            "prior_balance": prior_balance,
            "expectations": ExpectationSerializer(expectations, many=True).data,
            "transactions": TransactionSerializer(transactions, many=True).data,
            "totals": {
                "expectations": exp_total,
                "transactions": tx_total,
                "balance": balance,
            }
        })
