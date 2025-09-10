from rest_framework.routers import DefaultRouter
from .views import CategoryViewSet, TransactionViewSet, ExpectationViewSet, ReconciliationView
from django.urls import path, include

router = DefaultRouter()
router.register(r'categories', CategoryViewSet)
router.register(r'transactions', TransactionViewSet)
router.register(r'expectations', ExpectationViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('reconciliation/', ReconciliationView.as_view(), name="ledger-reconciliation"),
]