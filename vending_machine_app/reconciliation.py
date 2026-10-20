"""Lease queries outside DB locks; settle under product -> order -> payment locks."""
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .daraja import query_stk
from .inventory import consume, release
from .models import PaymentAttempt, Payment, Product, Order, DispenseRequest


def unsettled_attempts():
    return Q(status__in=["PROCESSING", "UNKNOWN"]) | Q(
        callback_received_at__isnull=False,
        verification_status__in=["UNVERIFIED", "PENDING", "ERROR", "MANUAL_REVIEW"],
    )


def result_data(attempt, outcome):
    return {"id": attempt.pk, "outcome": outcome, "verification_status": attempt.verification_status,
            "verification_message": attempt.verification_message,
            "verified_at": attempt.verified_at, "last_checked_at": attempt.last_checked_at}


def reconcile_attempt(pk):
    now = timezone.now()
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().get(pk=pk)
        if attempt.verification_status == "VERIFIED":
            return result_data(attempt, "ALREADY_VERIFIED")
        if (attempt.next_check_at and attempt.next_check_at > now) or (
            attempt.check_token and attempt.check_started_at and attempt.check_started_at > now - timedelta(minutes=2)
        ):
            return result_data(attempt, "DEFERRED")
        token = uuid4()
        attempt.check_token = token
        attempt.check_started_at = now
        attempt.next_check_at = now + timedelta(seconds=settings.MPESA_RECHECK_SECONDS)
        attempt.verification_status = "PENDING"
        attempt.save()
    # No database locks are held during the provider request.
    result = query_stk(attempt)
    found = Payment.objects.select_related("order").get(pk=attempt.payment_id)
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=found.order.product_id)
        order = Order.objects.select_for_update().get(pk=found.order_id)
        payment = Payment.objects.select_for_update().get(pk=found.pk)
        attempt = PaymentAttempt.objects.select_for_update().get(pk=pk)
        if attempt.check_token != token:
            return result_data(attempt, "SUPERSEDED")
        now = timezone.now()
        attempt.check_token = None
        attempt.check_started_at = None
        attempt.last_checked_at = now
        attempt.next_check_at = now + timedelta(seconds=settings.MPESA_RECHECK_SECONDS)
        state = result["state"]
        if state == "SUCCESS" and not (attempt.amount == payment.amount == order.total_amount):
            state = "MANUAL_REVIEW"
            result["message"] = "Stored order and request amounts disagree; operator investigation required."
        attempt.verification_status = {"SUCCESS": "VERIFIED"}.get(state, state)
        attempt.verification_message = result["message"]
        attempt.provider_result_code = result.get("code", "")
        if state == "SUCCESS":
            attempt.status = "SUCCESS"
            attempt.verified_at = now
            attempt.paid_at = attempt.paid_at or now
            attempt.mpesa_receipt_number = attempt.callback_receipt_number or attempt.mpesa_receipt_number
            attempt.result_code = "0"
            attempt.result_description = result["message"]
        elif state == "FAILED":
            attempt.status = "FAILED"
            attempt.result_code = result.get("code", "")
            attempt.result_description = result["message"]
        elif attempt.status != "SUCCESS":
            attempt.status = "UNKNOWN" if state in ("ERROR", "MANUAL_REVIEW") else "PROCESSING"
        attempt.save()
        if state == "SUCCESS":
            if payment.status == "SUCCESS":
                # Do not re-deduct historic stock or fulfill a second paid attempt.
                if payment.checkout_request_id == attempt.checkout_request_id:
                    payment.verified_at = now
                    payment.save(update_fields=["verified_at", "updated_at"])
            else:
                payment.status = "SUCCESS"
                payment.verified_at = now
                payment.paid_at = attempt.paid_at
                payment.checkout_request_id = attempt.checkout_request_id
                payment.merchant_request_id = attempt.merchant_request_id
                payment.phone_number = attempt.phone_number
                payment.mpesa_receipt_number = attempt.mpesa_receipt_number
                payment.result_code = "0"
                payment.result_description = result["message"]
                payment.save()
                dispense, _ = DispenseRequest.objects.get_or_create(order=order, defaults={"machine_slot": product.machine_slot})
                fulfilled = consume(order, product)
                order.status = "PAID" if fulfilled else "FAILED"
                order.save(update_fields=["status", "updated_at"])
                if not fulfilled:
                    dispense.status = "FAILED"
                    dispense.error_message = "Verified payment received but stock is unavailable or the order was cancelled. Operator review required."
                    dispense.save(update_fields=["status", "error_message"])
        elif state == "FAILED" and payment.status != "SUCCESS" and not payment.attempts.filter(unsettled_attempts()).exists():
            payment.status = "FAILED"
            payment.result_code = attempt.result_code
            payment.result_description = attempt.result_description
            payment.save()
            release(order, product)
        return result_data(attempt, state)
