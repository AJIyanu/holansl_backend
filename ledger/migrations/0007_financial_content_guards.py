"""Protect submitted detail rows and validate posted source identity at commit."""
from django.db import migrations

SQL=r'''
CREATE FUNCTION ledger_detail_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rowdata jsonb; parent uuid; state text;
BEGIN
  IF TG_OP='DELETE' THEN rowdata:=to_jsonb(OLD); ELSE rowdata:=to_jsonb(NEW); END IF;
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Financial detail history cannot be deleted' USING ERRCODE='23514'; END IF;
  IF TG_ARGV[0]='invoice' THEN SELECT record_id INTO parent FROM ledger_invoice WHERE id=(rowdata->>'invoice_id')::uuid;
  ELSIF TG_ARGV[0]='credit' THEN SELECT record_id INTO parent FROM ledger_creditnote WHERE id=(rowdata->>'credit_id')::uuid;
  ELSE parent:=(rowdata->>'record_id')::uuid; END IF;
  SELECT status INTO state FROM ledger_financialrecord WHERE id=parent;
  IF state<>'DRAFT' THEN RAISE EXCEPTION 'Submitted financial details are immutable' USING ERRCODE='23514'; END IF;
  IF TG_OP='UPDATE' THEN
    IF TG_ARGV[0]='invoice' AND to_jsonb(NEW)->'invoice_id'<>to_jsonb(OLD)->'invoice_id' THEN RAISE EXCEPTION 'Cannot move invoice lines' USING ERRCODE='23514';
    ELSIF TG_ARGV[0]='credit' AND to_jsonb(NEW)->'credit_id'<>to_jsonb(OLD)->'credit_id' THEN RAISE EXCEPTION 'Cannot move credit lines' USING ERRCODE='23514';
    ELSIF TG_ARGV[0]='record' AND to_jsonb(NEW)->'record_id'<>to_jsonb(OLD)->'record_id' THEN RAISE EXCEPTION 'Cannot move financial details' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE FUNCTION ledger_posted_source_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE obj ledger_financialrecord%ROWTYPE; j ledger_journalentry%ROWTYPE;
BEGIN
  SELECT * INTO obj FROM ledger_financialrecord WHERE id=NEW.id;
  IF obj.status IN ('POSTED','REVERSED') THEN
    IF obj.kind NOT IN ('ADVANCE','TAX_REMITTANCE') AND NOT EXISTS(SELECT 1 FROM ledger_journalentry WHERE source_id=obj.id AND event='POST' AND posted_at IS NOT NULL) THEN
      RAISE EXCEPTION 'Posted financial record requires its journal' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM ledger_financialrevision r JOIN ledger_approvalrequest a ON a.revision_id=r.id JOIN ledger_approvaldecision d ON d.request_id=a.id WHERE r.record_id=obj.id AND r.version=obj.version AND a.status='APPROVED' AND d.approved AND d.created_by_id=obj.approved_by_id) THEN
      RAISE EXCEPTION 'Posting requires the recorded revision approval' USING ERRCODE='23514';
    END IF;
    IF obj.status='REVERSED' AND NOT EXISTS(SELECT 1 FROM ledger_journalentry WHERE source_id=obj.id AND event='REVERSE' AND posted_at IS NOT NULL) THEN
      RAISE EXCEPTION 'Reversed record requires its reversal journal' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER ledger_posted_source AFTER INSERT OR UPDATE ON ledger_financialrecord DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_posted_source_check();
'''
TABLES={'invoice':'record','invoiceline':'invoice','receipt':'record','payment':'record','costline':'record','advance':'record','advanceclearance':'record','creditnote':'record','creditnoteline':'credit','withholdingrecord':'record','taxremittance':'record','manualline':'record'}


def forwards(apps,schema_editor):
    schema_editor.execute(SQL,params=None)
    for table,kind in TABLES.items():
        schema_editor.execute(f"CREATE TRIGGER ledger_{table}_content BEFORE INSERT OR UPDATE OR DELETE ON ledger_{table} FOR EACH ROW EXECUTE FUNCTION ledger_detail_guard('{kind}')")
    schema_editor.execute('CREATE TRIGGER ledger_invoiceissue_immutable BEFORE UPDATE OR DELETE ON ledger_invoiceissue FOR EACH ROW EXECUTE FUNCTION ledger_no_mutation()')
    schema_editor.execute('CREATE TRIGGER ledger_clientremittanceevidence_immutable BEFORE UPDATE OR DELETE ON ledger_clientremittanceevidence FOR EACH ROW EXECUTE FUNCTION ledger_no_mutation()')


def backwards(apps,schema_editor):
    schema_editor.execute('DROP FUNCTION ledger_detail_guard() CASCADE; DROP FUNCTION ledger_posted_source_check() CASCADE; DROP TRIGGER ledger_invoiceissue_immutable ON ledger_invoiceissue; DROP TRIGGER ledger_clientremittanceevidence_immutable ON ledger_clientremittanceevidence;')


class Migration(migrations.Migration):
    dependencies=[('ledger','0006_invoiceissue')]
    operations=[migrations.RunPython(forwards,backwards)]
