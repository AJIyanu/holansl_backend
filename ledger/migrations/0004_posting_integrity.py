"""Database-level posted-journal integrity and additive initial currency data."""
from django.db import migrations

SQL = r'''
CREATE FUNCTION ledger_no_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Ledger history is immutable' USING ERRCODE='23514';
END $$;

CREATE FUNCTION ledger_journal_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' OR OLD.posted_at IS NOT NULL THEN
    RAISE EXCEPTION 'Posted journal cannot be changed or deleted' USING ERRCODE='23514';
  END IF;
  IF NEW.posted_at IS NOT NULL THEN
    PERFORM 1 FROM ledger_accountingperiod WHERE id=NEW.period_id AND NOT closed FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Accounting period is closed' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_journal_immutable BEFORE UPDATE OR DELETE ON ledger_journalentry
FOR EACH ROW EXECUTE FUNCTION ledger_journal_guard();

CREATE FUNCTION ledger_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid;
BEGIN
  IF TG_OP='DELETE' THEN target:=OLD.entry_id; ELSE target:=NEW.entry_id; END IF;
  IF EXISTS(SELECT 1 FROM ledger_journalentry WHERE id=target AND posted_at IS NOT NULL)
     OR (TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM ledger_journalentry WHERE id=OLD.entry_id AND posted_at IS NOT NULL)) THEN
    RAISE EXCEPTION 'Posted journal lines cannot change' USING ERRCODE='23514';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END $$;
CREATE TRIGGER ledger_lines_immutable BEFORE INSERT OR UPDATE OR DELETE ON ledger_journalline
FOR EACH ROW EXECUTE FUNCTION ledger_line_guard();

CREATE FUNCTION ledger_check_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target uuid; head ledger_journalentry%ROWTYPE; n bigint; delta numeric; bad bigint;
BEGIN
  IF TG_TABLE_NAME='ledger_journalentry' THEN target:=NEW.id;
  ELSIF TG_OP='DELETE' THEN target:=OLD.entry_id; ELSE target:=NEW.entry_id; END IF;
  SELECT * INTO head FROM ledger_journalentry WHERE id=target;
  IF head.posted_at IS NULL THEN RETURN NULL; END IF;
  SELECT count(*),coalesce(sum(l.debit-l.credit),0),count(*) FILTER (WHERE a.currency_id<>head.currency_id OR l.debit<>round(l.debit,c.minor_units) OR l.credit<>round(l.credit,c.minor_units))
  INTO n,delta,bad FROM ledger_journalline l JOIN ledger_accountcurrency a ON a.id=l.account_id JOIN ledger_currency c ON c.id=head.currency_id WHERE l.entry_id=target;
  IF n<2 OR delta<>0 OR bad<>0 THEN
    RAISE EXCEPTION 'Journal must balance exactly in one currency at its minor units' USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM ledger_accountingperiod WHERE id=head.period_id AND head.date BETWEEN start AND "end") THEN
    RAISE EXCEPTION 'Journal date is outside its period' USING ERRCODE='23514';
  END IF;
  IF head.reversal_of_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ledger_journalentry WHERE id=head.reversal_of_id AND currency_id=head.currency_id AND posted_at IS NOT NULL) THEN
    RAISE EXCEPTION 'Invalid reversal journal' USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_balance_header AFTER INSERT OR UPDATE ON ledger_journalentry
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_balance();
CREATE CONSTRAINT TRIGGER ledger_balance_lines AFTER INSERT OR UPDATE OR DELETE ON ledger_journalline
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_balance();

CREATE FUNCTION ledger_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_data jsonb; new_data jsonb;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Financial records cannot be deleted' USING ERRCODE='23514'; END IF;
  IF NEW.status<>OLD.status AND NOT (
    (OLD.status='DRAFT' AND NEW.status IN ('SUBMITTED','SUPERSEDED')) OR
    (OLD.status='SUBMITTED' AND NEW.status IN ('APPROVED','REJECTED','SUPERSEDED')) OR
    (OLD.status='APPROVED' AND NEW.status IN ('POSTED','SUPERSEDED')) OR
    (OLD.status='REJECTED' AND NEW.status='SUPERSEDED') OR
    (OLD.status='POSTED' AND NEW.status='REVERSED')
  ) THEN RAISE EXCEPTION 'Invalid financial status transition' USING ERRCODE='23514'; END IF;
  IF OLD.status<>'DRAFT' THEN
    old_data:=to_jsonb(OLD)-ARRAY['status','approved_by_id','posted_at','registered_version_id'];
    new_data:=to_jsonb(NEW)-ARRAY['status','approved_by_id','posted_at','registered_version_id'];
    IF OLD.registered_version_id IS NULL AND NEW.registered_version_id IS NOT NULL THEN
      old_data:=old_data-'reference';new_data:=new_data-'reference';
    END IF;
    IF old_data<>new_data THEN RAISE EXCEPTION 'Submitted financial content is immutable' USING ERRCODE='23514'; END IF;
  END IF;
  IF OLD.posted_at IS NOT NULL AND (NEW.posted_at IS DISTINCT FROM OLD.posted_at OR NEW.status NOT IN ('POSTED','REVERSED')) THEN
    RAISE EXCEPTION 'Posted record cannot become a draft' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_financial_record_guard BEFORE UPDATE OR DELETE ON ledger_financialrecord
FOR EACH ROW EXECUTE FUNCTION ledger_record_guard();

CREATE FUNCTION ledger_account_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME='ledger_accountcurrency' THEN
    IF (NEW.account_id<>OLD.account_id OR NEW.currency_id<>OLD.currency_id) AND EXISTS(SELECT 1 FROM ledger_journalline WHERE account_id=OLD.id) THEN
      RAISE EXCEPTION 'Posted account currency identity cannot change' USING ERRCODE='23514';
    END IF;
  ELSIF TG_TABLE_NAME='ledger_currency' THEN
    IF (NEW.minor_units<>OLD.minor_units OR NEW.code<>OLD.code) AND EXISTS(SELECT 1 FROM ledger_journalentry WHERE currency_id=OLD.id) THEN
      RAISE EXCEPTION 'Posted currency identity cannot change' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ledger_account_currency_guard BEFORE UPDATE ON ledger_accountcurrency FOR EACH ROW EXECUTE FUNCTION ledger_account_identity_guard();
CREATE TRIGGER ledger_currency_guard BEFORE UPDATE ON ledger_currency FOR EACH ROW EXECUTE FUNCTION ledger_account_identity_guard();
'''

IMMUTABLE=['financialrevision','approvaldecision','ledgerevent','clientmovement','allocationevent','taxcollectionallocation','statementsnapshot','postingpolicyversion','taxpolicyversion','revenuerecognition','refundapplication']


def forwards(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        raise RuntimeError('Ledger integrity requires PostgreSQL.')
    schema_editor.execute(SQL, params=None)
    for table in IMMUTABLE:
        schema_editor.execute(f'CREATE TRIGGER ledger_{table}_immutable BEFORE UPDATE OR DELETE ON ledger_{table} FOR EACH ROW EXECUTE FUNCTION ledger_no_mutation()')
    Currency=apps.get_model('ledger','Currency')
    for code,name,symbol in [('NGN','Nigerian naira','₦'),('USD','US dollar','$'),('GBP','Pound sterling','£')]:
        Currency.objects.get_or_create(code=code,defaults={'name':name,'symbol':symbol,'minor_units':2,'active':True,'created_by_name':'System'})


def backwards(apps,schema_editor):
    # Keep currency data; dropping protections is an operator-reviewed rollback.
    schema_editor.execute('DROP FUNCTION ledger_no_mutation() CASCADE; DROP FUNCTION ledger_journal_guard() CASCADE; DROP FUNCTION ledger_line_guard() CASCADE; DROP FUNCTION ledger_check_balance() CASCADE; DROP FUNCTION ledger_record_guard() CASCADE; DROP FUNCTION ledger_account_identity_guard() CASCADE;')


class Migration(migrations.Migration):
    dependencies=[('ledger','0003_initial'),('procurement','0006_clientpurchaseorderline_item_and_more')]
    operations=[migrations.RunPython(forwards,backwards)]
