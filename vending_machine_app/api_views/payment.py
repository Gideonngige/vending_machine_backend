from decimal import InvalidOperation

from .common_imports import *

logger = logging.getLogger(__name__)


def get_daraja_urls():
    """Return URLs for the configured Daraja environment."""
    if os.environ.get("MPESA_ENVIRONMENT", "").lower() == "production":
        base_url = "https://api.safaricom.co.ke"
    else:
        base_url = "https://sandbox.safaricom.co.ke"

    return {
        "access_token_url": (
            f"{base_url}/oauth/v1/generate"
            "?grant_type=client_credentials"
        ),
        "stk_push_url": (
            f"{base_url}/mpesa/stkpush/v1/processrequest"
        ),
    }


def get_access_token():
    """Get an OAuth access token from Daraja."""
    urls = get_daraja_urls()

    try:
        response = requests.get(
            urls["access_token_url"],
            auth=(
                os.getenv("MPESA_CONSUMER_KEY"),
                os.getenv("MPESA_CONSUMER_SECRET"),
            ),
            timeout=30,
        )
        response.raise_for_status()

        token = response.json().get("access_token")
        if not token:
            logger.error("Daraja response did not contain an access token.")

        return token

    except (requests.RequestException, ValueError):
        logger.exception("Daraja access token request failed.")
        return None


def normalize_mpesa_phone(phone_number):
    """Convert a Kenyan phone number to 2547XXXXXXXX format."""
    if not phone_number:
        return None

    phone = (
        str(phone_number)
        .strip()
        .replace(" ", "")
        .replace("-", "")
    )

    if phone.startswith("+254"):
        phone = phone[1:]
    elif phone.startswith("0"):
        phone = "254" + phone[1:]
    elif phone.startswith("7") or phone.startswith("1"):
        phone = "254" + phone

    if (
        len(phone) != 12
        or not phone.isdigit()
        or not phone.startswith("254")
    ):
        return None

    return phone


def lipa_na_mpesa(phone_number, amount, order_reference):
    """Initiate an STK Push for a coffee vending order."""
    phone = normalize_mpesa_phone(phone_number)

    if not phone:
        return {
            "success": False,
            "message": "Invalid M-Pesa phone number.",
        }

    try:
        amount = Decimal(str(amount))
    except Exception:
        return {
            "success": False,
            "message": "Invalid payment amount.",
        }

    # Daraja STK Push accepts whole-number amounts.
    if amount <= 0 or amount != amount.to_integral_value():
        return {
            "success": False,
            "message": "Amount must be a positive whole number.",
        }

    access_token = get_access_token()
    if not access_token:
        return {
            "success": False,
            "message": "Failed to get M-Pesa access token.",
        }

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    shortcode = str(os.getenv("MPESA_SHORTCODE"))

    password = base64.b64encode(
        f"{shortcode}{os.getenv('MPESA_PASSKEY')}{timestamp}".encode()
    ).decode()

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": shortcode,
        "PhoneNumber": phone,
        "CallBackURL": os.getenv("MPESA_CALLBACK_URL"),
        "AccountReference": str(order_reference)[:12],
        "TransactionDesc": "Coffee vending payment",
    }

    try:
        response = requests.post(
            get_daraja_urls()["stk_push_url"],
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        try:
            data = response.json()
        except ValueError:
            return {
                "success": False,
                "message": "Invalid response from M-Pesa.",
            }

        if not response.ok:
            return {
                "success": False,
                "message": (
                    data.get("errorMessage")
                    or data.get("ResponseDescription")
                    or "M-Pesa request failed."
                ),
            }

        if data.get("ResponseCode") != "0" or not data.get(
            "CheckoutRequestID"
        ):
            return {
                "success": False,
                "message": (
                    data.get("ResponseDescription")
                    or "M-Pesa did not accept the payment request."
                ),
            }

        return {
            "success": True,
            "mpesa": data,
        }

    except requests.RequestException:
        logger.exception("Daraja STK Push request failed.")
        return {
            "success": False,
            "message": "Unable to contact M-Pesa. Please try again.",
        }




@api_view(["POST"])
@permission_classes([AllowAny])
def initiate_coffee_payment(request, reference):
    phone_number = request.data.get("phone_number")

    if not phone_number:
        return Response({
            "success": False,
            "message": "M-Pesa phone number is required.",
        }, status=400)

    order = Order.objects.filter(reference=reference).first()

    if not order:
        return Response({
            "success": False,
            "message": "Order not found.",
        }, status=404)

    if order.status != "PENDING":
        return Response({
            "success": False,
            "message": "This order is not awaiting payment.",
        }, status=400)

    payment = Payment.objects.filter(order=order).first()

    if not payment:
        return Response({
            "success": False,
            "message": "Payment record not found.",
        }, status=404)

    if payment.status == "SUCCESS":
        return Response({
            "success": False,
            "message": "This order has already been paid.",
        }, status=400)

    stk_result = lipa_na_mpesa(
        phone_number=phone_number,
        amount=payment.amount,
        order_reference=order.reference,
    )

    if not stk_result.get("success"):
        return Response({
            "success": False,
            "message": stk_result.get(
                "message", "Unable to initiate payment."
            ),
        }, status=400)

    mpesa_data = stk_result["mpesa"]

    payment.phone_number = phone_number
    payment.status = "PROCESSING"
    payment.checkout_request_id = mpesa_data["CheckoutRequestID"]
    payment.merchant_request_id = mpesa_data.get("MerchantRequestID")
    payment.save()

    return Response({
        "success": True,
        "message": (
            mpesa_data.get("CustomerMessage")
            or "Check your phone and enter your M-Pesa PIN."
        ),
        "order_reference": order.reference,
        "payment_status": payment.status,
        "checkout_request_id": payment.checkout_request_id,
    })




@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def mpesa_callback(request):
    try:
        # Reject malformed input before touching payment or stock records.
        data = request.data
        body = data.get("Body") if isinstance(data, dict) else None
        stk = body.get("stkCallback") if isinstance(body, dict) else None
        if not isinstance(stk, dict):
            return Response({"ResultCode": 1, "ResultDesc": "Invalid callback body"}, status=400)

        checkout_request_id = stk.get("CheckoutRequestID")
        result_code = stk.get("ResultCode")
        if (
            not isinstance(checkout_request_id, str) or not checkout_request_id.strip()
            or type(result_code) is not int or result_code < 0
            or not isinstance(stk.get("ResultDesc", ""), str)
        ):
            return Response({"ResultCode": 1, "ResultDesc": "Invalid callback fields"}, status=400)

        with transaction.atomic():
            payment = (
                Payment.objects
                .select_for_update()
                .select_related("order")
                .filter(checkout_request_id=checkout_request_id)
                .first()
            )

            if not payment:
                return Response({
                    "ResultCode": 1,
                    "ResultDesc": "Payment not found",
                })

            # Repeated success callbacks must not dispense twice.
            if payment.status == "SUCCESS":
                return Response({
                    "ResultCode": 0,
                    "ResultDesc": "Already processed",
                })

            order = payment.order

            if result_code == 0:
                callback_metadata = stk.get("CallbackMetadata")
                items = callback_metadata.get("Item") if isinstance(callback_metadata, dict) else None
                if not isinstance(items, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("Name"), str)
                    for item in items
                ):
                    return Response({"ResultCode": 1, "ResultDesc": "Invalid payment metadata"}, status=400)
                metadata = {item["Name"]: item.get("Value") for item in items}
                if len(metadata) != len(items):
                    return Response({"ResultCode": 1, "ResultDesc": "Duplicate metadata fields"}, status=400)
                try:
                    amount = Decimal(str(metadata.get("Amount")))
                except (InvalidOperation, ValueError):
                    amount = Decimal("NaN")
                receipt = metadata.get("MpesaReceiptNumber")
                if (
                    not amount.is_finite() or amount <= 0 or amount != payment.amount
                    or not isinstance(receipt, str) or not receipt.strip()
                    or len(receipt) > 50
                ):
                    return Response({"ResultCode": 1, "ResultDesc": "Payment amount or receipt invalid"}, status=400)

                payment.status = "SUCCESS"
                payment.mpesa_receipt_number = receipt.strip()
                payment.result_code = result_code
                payment.result_description = stk.get("ResultDesc", "")
                payment.paid_at = timezone.now()
                payment.save()

                order.status = "PAID"
                order.save(update_fields=["status"])

                dispense = (
                    DispenseRequest.objects
                    .select_for_update()
                    .filter(order=order)
                    .first()
                )

                # Payment authorizes preparation; only machine feedback can
                # establish that coffee is ready. Leave dispensing pending.

                # Decrement stock only on the first successful callback.

                product = Product.objects.select_for_update().get(
                    pk=order.product_id
                )

                if product.stock_quantity >= order.quantity:
                    product.stock_quantity -= order.quantity
                    product.save(update_fields=["stock_quantity"])
                else:
                    # Payment succeeded, but there is insufficient stock.
                    # Do not pretend the coffee has been dispensed.
                    if dispense:
                        dispense.status = "FAILED"
                        dispense.error_message = (
                            "Payment succeeded but stock is insufficient."
                        )
                        dispense.save(
                            update_fields=["status", "error_message"]
                        )

            else:
                payment.status = "FAILED"
                payment.result_code = result_code
                payment.result_description = stk.get("ResultDesc", "")
                payment.save()

                # Keep the order pending so the customer can retry.
                # Do not mark it paid or release a product.

        return Response({
            "ResultCode": 0,
            "ResultDesc": "Accepted",
        })

    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "M-Pesa callback processing failed."
        )
        return Response({
            "ResultCode": 1,
            "ResultDesc": "Server error",
        }, status=500)




