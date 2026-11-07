from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ledger", "0017_partyledgertransaction_and_more")]
    operations = [migrations.RunSQL(
        sql="""
        CREATE FUNCTION ledger_party_transaction_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Party ledger transactions are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER party_transaction_immutable BEFORE UPDATE OR DELETE
        ON ledger_partyledgertransaction FOR EACH ROW EXECUTE FUNCTION ledger_party_transaction_immutable();
        CREATE FUNCTION ledger_linked_cash_guard() RETURNS trigger AS $$
        BEGIN
            IF EXISTS(SELECT 1 FROM ledger_partyledgertransaction WHERE cash_entry_id = OLD.id) THEN
                RAISE EXCEPTION 'Linked payments cannot be changed independently';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER linked_cash_guard BEFORE UPDATE OR DELETE ON ledger_cashbookentry
        FOR EACH ROW EXECUTE FUNCTION ledger_linked_cash_guard();
        """,
        reverse_sql="""
        DROP TRIGGER linked_cash_guard ON ledger_cashbookentry;
        DROP FUNCTION ledger_linked_cash_guard();
        DROP TRIGGER party_transaction_immutable ON ledger_partyledgertransaction;
        DROP FUNCTION ledger_party_transaction_immutable();
        """,
    )]
