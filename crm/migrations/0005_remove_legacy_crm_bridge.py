from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "crm",
            "0004_sensitive_documents",
        ),
        (
            "ledger",
            "0002_remove_ledger_domain",
        ),
        (
            "procurement",
            "0002_remove_procurement_domain",
        ),
    ]

    operations = [
        migrations.DeleteModel(
            name="ContactPerson",
        ),
        migrations.RemoveField(
            model_name="party",
            name="name",
        ),
        migrations.RemoveField(
            model_name="party",
            name="party_type",
        ),
    ]
