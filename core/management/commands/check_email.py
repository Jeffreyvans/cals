"""Send a test email through the configured backend and report the result.

Usage::

    python manage.py check_email --to me@example.com

The API key is never printed; only the sanitized provider response is shown.
"""

from django.core.management.base import BaseCommand, CommandError

from core.notifications import send_platform_email


class Command(BaseCommand):
    help = "Check email delivery through the configured Django backend."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Address to receive the test email.")

    def handle(self, *args, **options):
        recipient = options["to"].strip()
        if not recipient or "@" not in recipient:
            raise CommandError("Give a valid address with --to.")

        message = send_platform_email(
            subject="EduSphere email check",
            body="This is a test message from EduSphere. No action is needed.\n",
            recipient=recipient,
        )
        self.stdout.write(f"Status: {message.status}")
        self.stdout.write(f"Provider: {message.provider or 'not attempted'}")
        self.stdout.write(f"Provider message id: {message.provider_message_id or '-'}")
        self.stdout.write(f"Provider response: {message.provider_response or '-'}")
