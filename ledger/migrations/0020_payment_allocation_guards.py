from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ledger", "0019_partyledgertransaction_allocation_request_and_more")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION ledger_validate_payment_allocation() RETURNS trigger AS $$
        DECLARE
            payment_row ledger_partyledgertransaction%ROWTYPE;
            document_row ledger_partyledgertransaction%ROWTYPE;
            used_payment numeric;
            used_document numeric;
        BEGIN
            SELECT * INTO payment_row FROM ledger_partyledgertransaction WHERE id = NEW.payment_id FOR UPDATE;
            SELECT * INTO document_row FROM ledger_partyledgertransaction WHERE id = NEW.document_id FOR UPDATE;
            IF payment_row.id IS NULL OR document_row.id IS NULL
               OR payment_row.transaction_type <> 'PAYMENT'
               OR document_row.transaction_type NOT IN ('INVOICE', 'BILL')
               OR payment_row.party_id <> document_row.party_id
               OR payment_row.ledger_kind <> document_row.ledger_kind
               OR payment_row.currency <> document_row.currency
               OR payment_row.date < document_row.date THEN
                RAISE EXCEPTION 'Invalid payment allocation scope';
            END IF;
            SELECT COALESCE(SUM(amount), 0) INTO used_payment FROM ledger_partypaymentallocation WHERE payment_id = NEW.payment_id;
            SELECT COALESCE(SUM(amount), 0) INTO used_document FROM ledger_partypaymentallocation WHERE document_id = NEW.document_id;
            IF used_payment + NEW.amount > payment_row.amount OR used_document + NEW.amount > document_row.amount THEN
                RAISE EXCEPTION 'Allocation exceeds payment or document balance';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER validate_payment_allocation BEFORE INSERT ON ledger_partypaymentallocation
        FOR EACH ROW EXECUTE FUNCTION ledger_validate_payment_allocation();
        CREATE TRIGGER payment_allocation_immutable BEFORE UPDATE OR DELETE ON ledger_partypaymentallocation
        FOR EACH ROW EXECUTE FUNCTION ledger_party_transaction_immutable();
        """,
        reverse_sql="""
        DROP TRIGGER payment_allocation_immutable ON ledger_partypaymentallocation;
        DROP TRIGGER validate_payment_allocation ON ledger_partypaymentallocation;
        DROP FUNCTION ledger_validate_payment_allocation();
        """,
    )]
