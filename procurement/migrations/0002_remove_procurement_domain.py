from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "procurement",
            "0001_initial",
        ),
        (
            "ledger",
            "0002_remove_ledger_domain",
        ),
    ]

    operations = [
        migrations.DeleteModel(
            name="POTracker",
        ),
        migrations.DeleteModel(
            name="PurchaseOrder",
        ),
        migrations.DeleteModel(
            name="SupplierQuote",
        ),
        migrations.DeleteModel(
            name="ClientRequest",
        ),
    ]
