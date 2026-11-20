"""Check provider configuration and send a test email through it.

Usage::

    python manage.py check_email --to me@example.com

API keys are never printed: only the provider name, the From address and the
sanitized provider response are shown. The command also prints the same
summary as the platform Email health page (last 50 messages and counts).
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from config.brand import PRODUCT_NAME
from core.email_types import EMAIL_CHECK, EMAIL_TYPE_LABELS
from core.models import OutboundMessage
from core.notifications import EMAIL_FAILURE_MESSAGE, send_platform_email


class Command(BaseCommand):
    help = "Check email delivery through the configured Django backend."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Address to receive the test email.")

    def handle(self, *args, **options):
        recipient = options["to"].strip()
        if not recipient or "@" not in recipient:
            raise CommandError("Give a valid address with --to.")

        self.stdout.write(f"Provider: {getattr(settings, 'EMAIL_PROVIDER', '?')}")
        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"From: {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"Site URL: {getattr(settings, 'SITE_URL', '')}")

        message = send_platform_email(
            subject=f"{PRODUCT_NAME} email check",
            body=f"This is a test message from {PRODUCT_NAME}. No action is needed.\n",
            recipient=recipient,
            email_type=EMAIL_CHECK,
        )
        self.stdout.write(f"Status: {message.status}")
        self.stdout.write(f"Provider: {message.provider or 'not attempted'}")
        self.stdout.write(f"Provider message id: {message.provider_message_id or '-'}")
        self.stdout.write(f"Provider response: {message.provider_response or '-'}")
        if message.status != OutboundMessage.Status.SENT:
            self.stderr.write(EMAIL_FAILURE_MESSAGE)

        self.stdout.write("\nLast 50 messages:")
        for row in OutboundMessage.all_objects.select_related("institution").order_by(
            "-created"
        )[:50]:
            error = (row.provider_response or "-").replace("\n", " ")[:120]
            self.stdout.write(
                f"  {row.created:%Y-%m-%d %H:%M} | {row.email_type} | "
                f"{row.recipient} | {row.status} | {error}"
            )

        self.stdout.write("\nCounts by type:")
        for row in OutboundMessage.all_objects.values("email_type").annotate(
            total=Count("pk")
        ).order_by("email_type"):
            label = EMAIL_TYPE_LABELS.get(row["email_type"], row["email_type"])
            self.stdout.write(f"  {label}: {row['total']}")
        self.stdout.write("\nCounts by status:")
        for row in OutboundMessage.all_objects.values("status").annotate(
            total=Count("pk")
        ).order_by("status"):
            self.stdout.write(f"  {row['status']}: {row['total']}")
