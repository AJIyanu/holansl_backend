import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Creates a superuser non-interactively if one does not exist.'

    def handle(self, *args, **options):
        User = get_user_model()
        
        # Read credentials from secure Render environment variables
        username = os.environ.get('DJANGO_SU_USERNAME')
        email = os.environ.get('DJANGO_SU_EMAIL')
        password = os.environ.get('DJANGO_SU_PASSWORD')

        if not (username and email and password):
            self.stdout.write(self.style.WARNING(
                'Skipping superuser creation: Missing DJANGO_SU_* environment variables.'
            ))
            return

        if not User.objects.filter(username=username).exists():
            self.stdout.write('Creating initial superuser...')
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" created successfully.'))
        else:
            self.stdout.write(f'Superuser "{username}" already exists. Skipping.')