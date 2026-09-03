from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from accounts.executive_permissions import bootstrap_executives


class Command(BaseCommand):
    help = "Preview the one-time CEO/CTO baseline; --apply explicitly grants all current permissions."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database", default="default")

    def handle(self, *args, **options):
        using = options["database"]
        if not options["apply"]:
            count = Permission.objects.using(using).count()
            self.stdout.write(
                f"Preview only: grant {count} current permissions to CEO and CTO. No changes made."
            )
            return
        count = bootstrap_executives(using=using)
        self.stdout.write(
            self.style.SUCCESS(
                f"Granted {count} permissions to CEO/CTO. Do not schedule this command; rerunning restores removed grants."
            )
        )
