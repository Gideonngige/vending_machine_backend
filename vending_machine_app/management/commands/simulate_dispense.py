from uuid import uuid4
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from vending_machine_app.dispensing import record_event
from vending_machine_app.models import Order, DispenseRequest


class Command(BaseCommand):
    help = "Record one simulated dispense observation (DEBUG only; never controls hardware)."

    def add_arguments(self, parser):
        parser.add_argument("reference")
        parser.add_argument("state", choices=["DISPENSING", "READY", "FAILED", "COLLECTED"])
        parser.add_argument("--event-id", default=None, help="Reuse the same UUID when retrying this event.")
        parser.add_argument("--message", default="")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Dispensing simulation is disabled outside development.")
        event_id = options["event_id"] or str(uuid4())
        try:
            order = record_event(options["reference"], options["state"], event_id,
                                 source="simulator", message=options["message"])
        except (ValueError, Order.DoesNotExist, DispenseRequest.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"SIMULATED {order.reference}: {order.status}; event ID {event_id}")
