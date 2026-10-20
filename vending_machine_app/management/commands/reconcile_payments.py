import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from vending_machine_app.models import PaymentAttempt
from vending_machine_app.reconciliation import reconcile_attempt


class Command(BaseCommand):
    help = "Independently query due M-Pesa attempts. Use --watch for a separate worker process."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 100 or options["interval"] < 1:
            raise CommandError("Use a limit of 1–100 and a positive interval.")
        try:
            while True:
                close_old_connections()
                now = timezone.now()
                due = PaymentAttempt.objects.filter(
                    Q(status__in=["PROCESSING", "UNKNOWN"]) | Q(verification_status="PENDING") |
                    Q(status="SUCCESS", verification_status="UNVERIFIED"),
                ).exclude(verification_status__in=["VERIFIED", "FAILED", "MANUAL_REVIEW"]).filter(
                    Q(next_check_at__isnull=True) | Q(next_check_at__lte=now),
                ).exclude(check_token__isnull=False, check_started_at__gt=now - timedelta(minutes=2))
                ids = list(due.order_by("created_at", "id").values_list("id", flat=True)[:options["limit"]])
                for pk in ids:
                    reconcile_attempt(pk)
                self.stdout.write(f"Processed {len(ids)} due payment attempts.")
                if not options["watch"]:
                    return
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("Reconciliation worker stopped.")
