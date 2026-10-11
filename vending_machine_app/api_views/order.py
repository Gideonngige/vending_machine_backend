from .common_imports import *
from .helper import *


@csrf_exempt
@api_view(["POST"])
def create_order(request):
    body = json_body(request)

    if body is None:
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
        product = get_object_or_404(
            Product.objects.select_for_update(),
            id=product_id,
            is_available=True,
        )

        if product.stock_quantity < quantity:
            return error_response("This product is currently out of stock.")

        total = product.price * quantity

        order = Order.objects.create(
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=quantity,
            total_amount=total,
            customer_phone=phone,
            status=Order.Status.PENDING,
        )

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
            "payment_mode": getattr(
                settings, "COFFEE_PAYMENT_MODE", "simulation"
            ),
        },
        status=201,
    )


@api_view(["GET"])
def order_detail(request, reference):
    order = get_object_or_404(
        Order.objects.select_related("product"),
        reference=reference,
    )

    return JsonResponse({
        "success": True,
        "order": order_payload(order),
    })




@csrf_exempt
@api_view(["POST"])
def collect_order(request, reference):
    body = json_body(request)

    if body is None:
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