from .common_imports import *
# from .helper import lipa_na_mpesa


def normalize_phone(value):
    phone = str(value).replace(" ", "").replace("-", "")
    if phone.startswith("+254"):
        phone = phone[1:]
    elif phone.startswith("07") and len(phone) == 10:
        phone = "254" + phone[1:]

    if len(phone) != 12 or not phone.startswith("2547") or not phone.isdigit():
        return None
    return phone


def authorized_order(request, reference):
    token = request.headers.get("X-Order-Token", "")
    return get_object_or_404(
        CoffeeOrder,
        reference=reference,
        order_token=token,
    )


def serialize_order(order):
    return {
        "reference": order.reference,
        "status": order.status,
        "product_name": order.product_name,
        "amount": str(order.amount),
        "quantity": order.quantity,
        "machine_id": str(order.machine_id),
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def create_order(request):
    phone = normalize_phone(request.data.get("phone_number", ""))
    if not phone:
        return Response(
            {"detail": "Enter a valid Kenyan M-Pesa phone number."},
            status=400,
        )

    try:
        product_id = int(request.data.get("machine_product_id"))
        quantity = int(request.data.get("quantity", 1))
    except (TypeError, ValueError):
        return Response({"detail": "Invalid product or quantity."}, status=400)

    if quantity != 1:
        return Response(
            {"detail": "The virtual machine currently supports one cup per order."},
            status=400,
        )

    with transaction.atomic():
        item = get_object_or_404(
            MachineProduct.objects.select_for_update().select_related(
                "machine", "machine__business", "product"
            ),
            pk=product_id,
            is_available=True,
            product__is_active=True,
            machine__is_active=True,
            machine__status="ONLINE",
            machine__machine_type="VIRTUAL",
            machine__business__status="ACTIVE",
        )

        if item.stock_quantity < quantity:
            return Response({"detail": "Coffee is out of stock."}, status=400)

        order = CoffeeOrder.objects.create(
            machine=item.machine,
            machine_product=item,
            product_name=item.product.name,
            phone_number=phone,
            quantity=quantity,
            amount=item.price * Decimal(quantity),
        )
        CoffeePayment.objects.create(order=order)
        DispenseJob.objects.create(order=order)

    return Response(
        {
            **serialize_order(order),
            "order_token": order.order_token,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def order_status(request, reference):
    order = authorized_order(request, reference)
    return Response(serialize_order(order))


@api_view(["POST"])
@permission_classes([AllowAny])
def collect_order(request, reference):
    with transaction.atomic():
        order = get_object_or_404(
            CoffeeOrder.objects.select_for_update(),
            reference=reference,
            order_token=request.headers.get("X-Order-Token", ""),
        )

        if order.status != "READY":
            return Response(
                {"detail": "Coffee is not ready for collection."},
                status=400,
            )

        order.status = "COLLECTED"
        order.save(update_fields=["status", "updated_at"])

        job = order.dispense_job
        job.status = "COLLECTED"
        job.save(update_fields=["status"])

    return Response({
        "message": "Virtual coffee collected successfully.",
        "status": "COLLECTED",
    })





@api_view(["POST"])
@permission_classes([AllowAny])
def initiate_payment(request, reference):
    with transaction.atomic():
        order = get_object_or_404(
            CoffeeOrder.objects.select_for_update(),
            reference=reference,
            order_token=request.headers.get("X-Order-Token", ""),
        )

        if order.status != "PENDING":
            return Response(
                {"detail": "Payment has already been initiated for this order."},
                status=409,
            )

        order.status = "PAYMENT_REQUESTED"
        order.save(update_fields=["status", "updated_at"])

    try:
        response = lipa_na_mpesa(
            phone_number=order.phone_number,
            amount=order.amount,
            order_reference=order.reference,
        )

        if str(response.get("ResponseCode")) != "0":
            raise ValueError(
                response.get("ResponseDescription", "STK Push was rejected.")
            )

        checkout_id = response.get("CheckoutRequestID")
        if not checkout_id:
            raise ValueError("Daraja returned no CheckoutRequestID.")

        # Do not overwrite a callback that may have arrived very quickly.
        with transaction.atomic():
            payment = CoffeePayment.objects.select_for_update().get(
                order=order
            )
            payment.checkout_request_id = checkout_id
            payment.merchant_request_id = response.get(
                "MerchantRequestID", ""
            )
            if payment.status == "PENDING":
                payment.status = "PROCESSING"
            payment.save()

        return Response({
            "message": "STK Push sent. Approve payment on your phone.",
            "reference": order.reference,
        })

    except Exception:
        # An HTTP error or timeout does not prove payment failed.
        # Leave the order for reconciliation instead of issuing a second charge.
        import logging
        logging.getLogger(__name__).exception(
            "Could not confirm STK initiation for %s", reference
        )
        return Response({
            "detail": (
                "Payment initiation could not be confirmed. "
                "Check this order before retrying."
            )
        }, status=502)







@api_view(["POST"])
@permission_classes([AllowAny])
def mpesa_callback(request):
    try:
        callback = request.data["Body"]["stkCallback"]
        checkout_id = callback["CheckoutRequestID"]
        result_code = int(callback["ResultCode"])

        with transaction.atomic():
            payment = (
                CoffeePayment.objects.select_for_update()
                .select_related("order")
                .filter(checkout_request_id=checkout_id)
                .first()
            )

            if not payment:
                logger.error(
                    "Unmatched M-Pesa callback: %s",
                    checkout_id,
                )
                return Response({
                    "ResultCode": 0,
                    "ResultDesc": "Received",
                })

            if payment.status == "SUCCESS":
                return Response({
                    "ResultCode": 0,
                    "ResultDesc": "Already processed",
                })

            order = CoffeeOrder.objects.select_for_update().get(
                pk=payment.order_id
            )

            if result_code != 0:
                payment.status = "FAILED"
                payment.result_code = str(result_code)
                payment.result_description = callback.get(
                    "ResultDesc", ""
                )
                payment.save()

                if order.status not in ("PAID", "READY", "COLLECTED"):
                    order.status = "FAILED"
                    order.save(update_fields=["status", "updated_at"])

                return Response({
                    "ResultCode": 0,
                    "ResultDesc": "Received",
                })

            metadata = callback.get(
                "CallbackMetadata", {}
            ).get("Item", [])

            values = {
                item.get("Name"): item.get("Value")
                for item in metadata
            }

            payment.status = "SUCCESS"
            payment.result_code = "0"
            payment.receipt = str(
                values.get("MpesaReceiptNumber", "")
            )
            payment.paid_at = timezone.now()
            payment.save()

            order.status = "PAID"
            order.save(update_fields=["status", "updated_at"])

        logger.info("Payment successful: %s", order.reference)

        return Response({
            "ResultCode": 0,
            "ResultDesc": "Received",
        })

    except Exception:
        logger.exception("M-Pesa callback processing error")
        return Response({
            "ResultCode": 1,
            "ResultDesc": "Processing error",
        }, status=500)

