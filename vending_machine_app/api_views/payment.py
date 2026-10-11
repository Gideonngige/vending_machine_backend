from .common_imports import *


@csrf_exempt
@require_POST
def simulate_payment(request, reference):
    # This endpoint must never be enabled in production.
    if not settings.DEBUG or getattr(
        settings, "COFFEE_PAYMENT_MODE", "simulation"
    ) != "simulation":
        return error_response(
            "Payment simulation is disabled.",
            status=403,
        )

    body = json_body(request)

    if body is None:
        return error_response("Invalid JSON body.")

    result = body.get("result", "success")

    if result not in ("success", "failure"):
        return error_response("Result must be success or failure.")

    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update().select_related("product"),
            reference=reference,
        )
        payment = Payment.objects.select_for_update().get(order=order)
        dispense = DispenseRequest.objects.select_for_update().get(order=order)

        if payment.status == Payment.Status.SUCCESS:
            return JsonResponse({
                "success": True,
                "message": "Payment was already confirmed.",
                "order": order_payload(order),
            })

        if order.status != Order.Status.PENDING:
            return error_response(
                f"Order cannot be paid in its current state: {order.status}."
            )

        if result == "failure":
            payment.status = Payment.Status.FAILED
            payment.result_code = "SIMULATED_FAILURE"
            payment.result_description = "Demo payment failed."
            payment.save()

            order.status = Order.Status.FAILED
            order.save()

            return JsonResponse({
                "success": True,
                "message": "Simulated payment failure.",
                "order": order_payload(order),
            })

        product = Product.objects.select_for_update().get(
            pk=order.product_id
        )

        if product.stock_quantity < order.quantity:
            payment.status = Payment.Status.FAILED
            payment.result_code = "OUT_OF_STOCK"
            payment.result_description = (
                "Product stock is no longer available."
            )
            payment.save()

            order.status = Order.Status.FAILED
            order.save()

            return error_response(
                "Product is no longer available. No dispensing was authorized.",
                status=409,
            )

        # Demo only. Real payment success must come from
        # a verified Safaricom callback.
        payment.status = Payment.Status.SUCCESS
        payment.result_code = "0"
        payment.result_description = "SIMULATED payment successful."
        payment.paid_at = timezone.now()
        payment.mpesa_receipt_number = ""
        payment.save()

        order.status = Order.Status.READY
        order.save()

        dispense.status = DispenseRequest.Status.READY
        dispense.completed_at = timezone.now()
        dispense.save()

        product.stock_quantity -= order.quantity
        product.save(update_fields=["stock_quantity", "updated_at"])

    return JsonResponse({
        "success": True,
        "message": "Demo payment successful. Product is ready for collection.",
        "order": order_payload(order),
    })
