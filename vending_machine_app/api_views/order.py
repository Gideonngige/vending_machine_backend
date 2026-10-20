from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view

from vending_machine_app.models import Machine, Product, Order, Payment, DispenseRequest
from vending_machine_app.reconciliation import unsettled_attempts
from vending_machine_app.inventory import available_quantity, reserve, release
from .helper import json_body, normalize_phone, error_response, order_payload


@api_view(["POST"])
def create_order(request):
    body = json_body(request)

    if not isinstance(body, dict):
        return error_response("Invalid JSON body.")

    try:
        product_id = int(body.get("product_id"))
        quantity = int(body.get("quantity", 1))
    except (TypeError, ValueError):
        return error_response("A valid product_id and quantity are required.")

    if quantity < 1 or quantity > 5:
        return error_response("Quantity must be between 1 and 5.")

    phone = normalize_phone(body.get("phone"))

    if not phone:
        return error_response("Enter a valid Kenyan phone number.")

    with transaction.atomic():
        machine = get_object_or_404(
            Machine.objects.select_for_update(),
            code=body.get("machine_code", "MACHINE-001"), is_enabled=True,
        )
        product = get_object_or_404(
            Product.objects.select_for_update(),
            id=product_id,
            is_available=True,
            machine=machine,
        )

        if available_quantity(product) < quantity:
            return error_response("This product is currently out of stock or reserved.", status=409)

        total = product.price * quantity

        order = Order.objects.create(
            product=product,
            machine=machine,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            total_amount=total,
            customer_phone=phone,
            status=Order.Status.PENDING,
        )

        reserve(order, product)

        Payment.objects.create(
            order=order,
            phone_number=phone,
            amount=total,
            method="MPESA",
            status=Payment.Status.PENDING,
        )

        DispenseRequest.objects.create(
            order=order,
            machine_slot=product.machine_slot,
            status=DispenseRequest.Status.PENDING,
        )

    return JsonResponse(
        {
            "success": True,
            "message": "Order created. Payment has not yet been confirmed.",
            "order": order_payload(order),
        },
        status=201,
    )


@api_view(["GET"])
def order_detail(request, reference):
    orders = Order.objects.select_related("product", "machine")
    if request.query_params.get("machine"):
        orders = orders.filter(machine__code=request.query_params["machine"])
    order = get_object_or_404(
        orders,
        reference=reference,
    )

    return JsonResponse({
        "success": True,
        "order": order_payload(order),
    })




@api_view(["POST"])
def collect_order(request, reference):
    body = json_body(request)

    if not isinstance(body, dict):
        return error_response("Invalid JSON body.")

    # In a real machine, protect this operation with machine
    # authentication or a verified collection mechanism.
    if not settings.DEBUG:
        return error_response(
            "Manual collection is disabled outside development.",
            status=403,
        )

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            reference=reference,
        )
        dispense = DispenseRequest.objects.select_for_update().get(
            order=order
        )

        payment = getattr(order, "payment", None)
        if not payment or payment.status != "SUCCESS" or not payment.verified_at:
            return error_response("Payment must be independently verified before collection.", status=409)

        if order.status != Order.Status.READY:
            return error_response(
                "This order is not ready for collection.",
                status=409,
            )

        dispense.status = DispenseRequest.Status.COLLECTED
        dispense.collected_at = timezone.now()
        dispense.save()

        order.status = Order.Status.COLLECTED
        order.save()

    return JsonResponse({
        "success": True,
        "message": "Order marked as collected.",
        "order": order_payload(order),
    })

@api_view(["POST"])
def cancel_order(request, reference):
    # Lock in the same product -> order -> payment order as callbacks.
    found = get_object_or_404(Order, reference=reference)
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=found.product_id)
        order = Order.objects.select_for_update().get(pk=found.pk)
        payment = Payment.objects.select_for_update().filter(order=order).first()
        if order.status == Order.Status.CANCELLED:
            return JsonResponse({"success": True, "order": order_payload(order)})
        if order.status != Order.Status.PENDING or (payment and payment.status == "SUCCESS"):
            return error_response("Only unpaid pending orders can be cancelled.", status=409)
        if payment and payment.attempts.filter(unsettled_attempts()).exists():
            return error_response("A payment is still pending. Check its result before cancelling.", status=409)
        release(order, product)
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
    return JsonResponse({"success": True, "message": "Order cancelled; stock released.", "order": order_payload(order)})
