"""Read-only, staff-only transaction investigation."""
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from vending_machine_app.models import Machine, Order, Payment
from vending_machine_app.reconciliation import reconcile_attempt
from vending_machine_app.models import PaymentAttempt


class Filters(serializers.Serializer):
    machine = serializers.CharField(required=False, max_length=50)
    reference = serializers.CharField(required=False, max_length=20)
    payment_status = serializers.ChoiceField(choices=Payment.Status.values, required=False)
    order_status = serializers.ChoiceField(choices=Order.Status.values, required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    attention = serializers.ChoiceField(choices=["paid_not_dispensed", "extra_payments", "uncertain", "dispense_failed", "unverified"], required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=100, required=False)

    def validate(self, attrs):
        if attrs.get("start_date") and attrs.get("end_date") and attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError("End date must be on or after start date.")
        return attrs


def records():
    return Order.objects.select_related("machine", "payment", "dispense_request").annotate(
        successful_attempts=Count("payment__attempts", filter=Q(payment__attempts__status="SUCCESS"), distinct=True),
        uncertain_attempts=Count("payment__attempts", filter=Q(payment__attempts__status="UNKNOWN"), distinct=True),
    ).order_by("-created_at", "-id")


def transaction_data(order, detail=False):
    payment = getattr(order, "payment", None)
    dispense = getattr(order, "dispense_request", None)
    flags = []
    if payment and payment.status == "SUCCESS" and not (
        dispense and dispense.status in ("READY", "COLLECTED") and dispense.completed_at
    ):
        flags.append("paid_not_dispensed")
    if order.successful_attempts > 1:
        flags.append("extra_payments")
    if order.uncertain_attempts:
        flags.append("uncertain")
    if dispense and dispense.status == "FAILED":
        flags.append("dispense_failed")
    if payment and payment.status == "SUCCESS" and not payment.verified_at:
        flags.append("unverified")
    data = {
        "reference": order.reference, "machine_code": order.machine.code,
        "machine_name": order.machine.name, "product_name": order.product_name,
        "quantity": order.quantity, "total_amount": str(order.total_amount),
        "customer_phone": order.customer_phone, "order_status": order.status,
        "created_at": order.created_at, "attention": flags,
        "payment": None if not payment else {
            "id": payment.pk, "status": payment.status, "amount": str(payment.amount),
            "receipt": payment.mpesa_receipt_number, "paid_at": payment.paid_at,
            "result_description": payment.result_description,
            "verified_at": payment.verified_at, "receipt_verified": False,
        },
        "dispense": None if not dispense else {
            "status": dispense.status, "machine_slot": dispense.machine_slot,
            "started_at": dispense.started_at, "completed_at": dispense.completed_at,
            "collected_at": dispense.collected_at, "error_message": dispense.error_message,
        },
    }
    if detail:
        data["recovery_notes"] = [{"id": n.pk, "action": n.action, "note": n.note,
                                   "actor": n.actor.get_username(), "created_at": n.created_at}
                                  for n in order.recovery_notes.select_related("actor").order_by("-id")]
        data["dispense_events"] = [] if not dispense else [{
            "event_id": e.event_id, "state": e.state, "source": e.source,
            "message": e.message, "created_at": e.created_at,
        } for e in dispense.events.order_by("id")]
        data["attempts"] = [] if not payment else [{
            "id": attempt.pk, "status": attempt.status, "amount": str(attempt.amount),
            "phone_number": attempt.phone_number, "checkout_request_id": attempt.checkout_request_id,
            "merchant_request_id": attempt.merchant_request_id, "receipt": attempt.mpesa_receipt_number,
            "result_code": attempt.result_code, "result_description": attempt.result_description,
            "created_at": attempt.created_at, "paid_at": attempt.paid_at,
            "verification_status": attempt.verification_status,
            "verification_message": attempt.verification_message,
            "verified_at": attempt.verified_at, "last_checked_at": attempt.last_checked_at,
            "callback_received_at": attempt.callback_received_at,
            "provider_result_code": attempt.provider_result_code,
            "reported_receipt": attempt.callback_receipt_number,
            "receipt_verified": False,
        } for attempt in payment.attempts.all()]
    return data


class Pages(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


@never_cache
@api_view(["GET"])
@permission_classes([IsAdminUser])
def transaction_list(request):
    filters = Filters(data=request.query_params)
    filters.is_valid(raise_exception=True)
    values = filters.validated_data
    orders = records()
    if values.get("machine"):
        orders = orders.filter(machine=get_object_or_404(Machine, code=values["machine"]))
    if values.get("reference"):
        orders = orders.filter(reference__icontains=values["reference"])
    for key in ("payment_status", "order_status"):
        if key in values:
            orders = orders.filter(**{"payment__status" if key == "payment_status" else "status": values[key]})
    zone = timezone.get_current_timezone()
    if "start_date" in values:
        orders = orders.filter(created_at__gte=timezone.make_aware(datetime.combine(values["start_date"], time.min), zone))
    if "end_date" in values:
        try:
            end = values["end_date"] + timedelta(days=1)
        except OverflowError:
            raise serializers.ValidationError({"end_date": "Date is out of range."})
        orders = orders.filter(created_at__lt=timezone.make_aware(datetime.combine(end, time.min), zone))
    attention = values.get("attention")
    if attention == "paid_not_dispensed":
        orders = orders.filter(payment__status="SUCCESS").exclude(
            dispense_request__status__in=["READY", "COLLECTED"], dispense_request__completed_at__isnull=False,
        )
    elif attention == "extra_payments":
        orders = orders.filter(successful_attempts__gt=1)
    elif attention == "uncertain":
        orders = orders.filter(uncertain_attempts__gt=0)
    elif attention == "dispense_failed":
        orders = orders.filter(dispense_request__status="FAILED")
    elif attention == "unverified":
        orders = orders.filter(payment__status="SUCCESS", payment__verified_at__isnull=True)
    pages = Pages()
    page = pages.paginate_queryset(orders, request)
    response = pages.get_paginated_response([transaction_data(order) for order in page])
    response.data["timezone"] = str(zone)
    return response


@never_cache
@api_view(["GET"])
@permission_classes([IsAdminUser])
def transaction_detail(request, reference):
    order = get_object_or_404(records().prefetch_related("payment__attempts"), reference=reference)
    return Response(transaction_data(order, detail=True))


@never_cache
@api_view(["POST"])
@permission_classes([IsAdminUser])
def reconcile_payment_attempt(request, pk):
    get_object_or_404(PaymentAttempt, pk=pk)
    return Response(reconcile_attempt(pk))


class RecoveryInput(serializers.Serializer):
    action = serializers.ChoiceField(choices=["INVESTIGATING", "REFUND_REQUIRED", "REFUND_RECORDED", "RESOLVED"])
    note = serializers.CharField(max_length=1000, allow_blank=False)


@never_cache
@api_view(["POST"])
@permission_classes([IsAdminUser])
def add_recovery_note(request, reference):
    from django.db import transaction
    from vending_machine_app.models import RecoveryNote
    values = RecoveryInput(data=request.data)
    values.is_valid(raise_exception=True)
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), reference=reference)
        payment = Payment.objects.filter(order=order, status="SUCCESS").first()
        if not payment:
            raise serializers.ValidationError("Recovery records require a recorded successful payment.")
        note = RecoveryNote.objects.create(order=order, actor=request.user, **values.validated_data)
    return Response({"id": note.pk, "message": "Recovery note recorded. No refund or dispensing command was issued."}, status=201)
