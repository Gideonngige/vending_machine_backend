"""Confirmed observations only; no physical commands or automatic retries."""
from uuid import UUID
from django.db import transaction
from django.utils import timezone
from .models import Product, Order, Payment, DispenseRequest, DispenseEvent


class TransitionError(ValueError):
    pass


TRANSITIONS = {
    "PENDING": {"DISPENSING"},
    "DISPENSING": {"READY", "FAILED"},
    "READY": {"COLLECTED"},
}


def record_event(reference, state, event_id, *, source, message=""):
    event_id = UUID(str(event_id))
    if source not in {"simulator", "development_collection"}:
        raise TransitionError("A supported event source is required.")
    found = Order.objects.get(reference=reference)
    with transaction.atomic():
        Product.objects.select_for_update().get(pk=found.product_id)
        order = Order.objects.select_for_update().get(pk=found.pk)
        payment = Payment.objects.select_for_update().filter(order=order).first()
        dispense = DispenseRequest.objects.select_for_update().get(order=order)
        previous = DispenseEvent.objects.filter(event_id=event_id).first()
        if previous:
            if (previous.dispense_id, previous.state, previous.source, previous.message) != (dispense.pk, state, source, message):
                raise TransitionError("Event ID was already used with different contents.")
            return order
        if not payment or payment.status != "SUCCESS" or not payment.verified_at:
            raise TransitionError("Payment must be independently verified before dispensing.")
        if state == "DISPENSING" and not order.machine.is_enabled:
            raise TransitionError("The machine is disabled.")
        expected_order = "PAID" if dispense.status == "PENDING" else dispense.status
        if order.status != expected_order or state not in TRANSITIONS.get(dispense.status, set()):
            raise TransitionError(f"Cannot move {order.status}/{dispense.status} to {state}.")
        if state == "FAILED" and not message.strip():
            raise TransitionError("A failure reason is required.")
        if len(message) > 1000:
            raise TransitionError("Message must be at most 1000 characters.")
        now = timezone.now()
        if state == "DISPENSING":
            dispense.started_at = now
        elif state == "READY":
            dispense.completed_at = now
        elif state == "COLLECTED":
            dispense.collected_at = now
        elif state == "FAILED":
            dispense.error_message = message
        dispense.status = state
        dispense.save()
        order.status = state
        order.save(update_fields=["status", "updated_at"])
        DispenseEvent.objects.create(dispense=dispense, event_id=event_id, state=state, source=source, message=message)
        return order
