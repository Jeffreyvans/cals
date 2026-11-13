"""Send due webhook deliveries.

Run it from cron/systemd or a Celery beat entry, for example every minute::

    python manage.py deliver_webhooks

``--id`` sends one delivery regardless of its schedule; ``--now`` is accepted
so a scheduler can pass a simulated clock in tests.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import WebhookDelivery
from api.webhooks import deliver_due


class Command(BaseCommand):
    help = "Send queued webhook deliveries and retry the failed ones that are due."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--id", dest="delivery_id", type=int, default=None)
        parser.add_argument(
            "--now",
            default=None,
            help="Treat this ISO-8601 timestamp as the current time.",
        )

    def handle(self, *args, **options):
        now = parse_datetime(options["now"]) if options["now"] else timezone.now()
        if now is None:
            now = timezone.now()
        sent = deliver_due(limit=options["limit"], delivery_id=options["delivery_id"], now=now)
        pending = WebhookDelivery.objects.filter(
            status__in=[WebhookDelivery.Status.PENDING, WebhookDelivery.Status.FAILED]
        ).count()
        self.stdout.write(
            self.style.SUCCESS(f"Sent {sent} webhook delivery(ies); {pending} still queued.")
        )
