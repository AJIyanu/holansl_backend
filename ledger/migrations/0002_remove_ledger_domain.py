from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "ledger",
            "0001_initial",
        ),
    ]

    operations = [
        migrations.DeleteModel(
            name="Transaction",
        ),
        migrations.DeleteModel(
            name="Expectation",
        ),
        migrations.DeleteModel(
            name="Category",
        ),
    ]
