from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ledger", "0014_initial")]
    operations = [
        migrations.RunSQL(
            sql="""
            CREATE FUNCTION ledger_cash_journal_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Cash-book journals are immutable; record a reversal instead';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER cash_journal_immutable
            BEFORE UPDATE OR DELETE ON ledger_cashbookjournal
            FOR EACH ROW EXECUTE FUNCTION ledger_cash_journal_immutable();
            """,
            reverse_sql="""
            DROP TRIGGER cash_journal_immutable ON ledger_cashbookjournal;
            DROP FUNCTION ledger_cash_journal_immutable();
            """,
        ),
    ]
