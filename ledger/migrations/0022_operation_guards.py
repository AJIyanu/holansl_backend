from django.db import migrations
from importlib import import_module

TABLES = ('allocationrelease', 'ledgeroperation', 'reconciliation', 'reconciliationreview', 'invoicefulfilment', 'revenuerecognition', 'procurementledgerlink')
SQL = """
CREATE OR REPLACE FUNCTION ledger_validate_payment_allocation() RETURNS trigger AS $$
DECLARE p ledger_partyledgertransaction%ROWTYPE; d ledger_partyledgertransaction%ROWTYPE;
 used_p numeric; used_d numeric; refunded numeric; credited numeric;
BEGIN
 SELECT * INTO p FROM ledger_partyledgertransaction WHERE id=NEW.payment_id FOR UPDATE;
 SELECT * INTO d FROM ledger_partyledgertransaction WHERE id=NEW.document_id FOR UPDATE;
 IF p.id IS NULL OR d.id IS NULL OR p.transaction_type <> 'PAYMENT' OR d.transaction_type NOT IN ('INVOICE','BILL')
 OR p.party_id <> d.party_id OR p.ledger_kind <> d.ledger_kind OR p.currency <> d.currency
 OR NEW.effective_date IS NULL OR NEW.effective_date < p.date OR NEW.effective_date < d.date
 OR EXISTS(SELECT 1 FROM ledger_partyledgertransaction WHERE reverses_id IN (p.id,d.id)) THEN
 RAISE EXCEPTION 'Invalid allocation scope'; END IF;
 SELECT COALESCE(SUM(a.amount),0) INTO used_p FROM ledger_partypaymentallocation a WHERE a.payment_id=p.id AND NOT EXISTS(SELECT 1 FROM ledger_allocationrelease r WHERE r.allocation_id=a.id);
 SELECT COALESCE(SUM(a.amount),0) INTO used_d FROM ledger_partypaymentallocation a WHERE a.document_id=d.id AND NOT EXISTS(SELECT 1 FROM ledger_allocationrelease r WHERE r.allocation_id=a.id);
 SELECT COALESCE(SUM(amount),0) INTO refunded FROM ledger_partyledgertransaction WHERE related_transaction_id=p.id AND transaction_type='REFUND';
 SELECT COALESCE(SUM(amount),0) INTO credited FROM ledger_partyledgertransaction WHERE related_transaction_id=d.id AND transaction_type='CREDIT';
 IF used_p+refunded+NEW.amount > p.amount OR used_d+credited+NEW.amount > d.amount THEN RAISE EXCEPTION 'Allocation exceeds available balance'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
"""

# Restore the previous validation function before the added columns are removed.
OLD_SQL = import_module('ledger.migrations.0020_payment_allocation_guards').Migration.operations[0].sql
OLD_FUNCTION = OLD_SQL[OLD_SQL.index('CREATE FUNCTION'):OLD_SQL.index('CREATE TRIGGER')].replace('CREATE FUNCTION', 'CREATE OR REPLACE FUNCTION', 1)

class Migration(migrations.Migration):
    dependencies = [('ledger', '0021_allocationrelease_financesubmission_and_more')]
    operations = [migrations.RunSQL(SQL, OLD_FUNCTION)] + [
        migrations.RunSQL(
            f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON ledger_{table} FOR EACH ROW EXECUTE FUNCTION ledger_party_transaction_immutable();',
            f'DROP TRIGGER {table}_immutable ON ledger_{table};'
        ) for table in TABLES
    ]
