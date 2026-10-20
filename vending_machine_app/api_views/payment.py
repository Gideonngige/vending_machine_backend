import logging
import os
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from vending_machine_app.models import PaymentAttempt, Payment, Order, Product, DispenseRequest
from vending_machine_app.inventory import reserve, release
from vending_machine_app.reconciliation import unsettled_attempts
from vending_machine_app.daraja import get_daraja_urls, get_access_token, stk_credentials, environment

logger = logging.getLogger(__name__)


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

    shortcode = str(os.getenv("MPESA_SHORTCODE"))
    timestamp, password = stk_credentials(shortcode)

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
                "uncertain": True,
            }

        if not isinstance(data, dict):
            return {"success": False, "uncertain": True, "message": "Unexpected M-Pesa response. Check this order before retrying."}

        if not response.ok:
            return {
                "success": False,
                "uncertain": response.status_code >= 500,
                "message": (
                    data.get("errorMessage")
                    or data.get("ResponseDescription")
                    or "M-Pesa request failed."
                ),
            }

        if str(data.get("ResponseCode")) != "0" or not data.get(
            "CheckoutRequestID"
        ):
            return {
                "success": False,
                "uncertain": data.get("ResponseCode") is None or str(data.get("ResponseCode")) == "0",
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
            "message": "M-Pesa outcome is unknown. Check this order before retrying.",
            "uncertain": True,
        }




@api_view(["POST"])
@permission_classes([AllowAny])
@transaction.atomic
def initiate_coffee_payment(request, reference):
    phone_number = normalize_mpesa_phone(request.data.get("phone_number")) if isinstance(request.data, dict) else None

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

    product = Product.objects.select_for_update().get(pk=order.product_id)
    order = Order.objects.select_for_update().get(pk=order.pk)

    if order.status != "PENDING":
        return Response({
            "success": False,
            "message": "This order is not awaiting payment.",
        }, status=400)

    payment = Payment.objects.select_for_update().filter(order=order).first()

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

    if not order.machine.is_enabled:
        return Response({"success": False, "message": "This machine is disabled."}, status=400)

    if payment.attempts.filter(unsettled_attempts()).exists():
        return Response({"success": False, "message": "A payment attempt is still pending. Check this order before retrying."}, status=409)

    if not reserve(order, product):
        return Response({"success": False, "message": "Stock is no longer available for this order."}, status=409)

    stk_result = lipa_na_mpesa(
        phone_number=phone_number,
        amount=payment.amount,
        order_reference=order.reference,
    )

    if not stk_result.get("success"):
        PaymentAttempt.objects.create(
            payment=payment, phone_number=phone_number, amount=payment.amount,
            status="UNKNOWN" if stk_result.get("uncertain") else "FAILED",
            result_description=stk_result.get("message", "Unable to initiate payment."),
        )
        payment.status = "PROCESSING" if stk_result.get("uncertain") else "FAILED"
        payment.save(update_fields=["status", "updated_at"])
        if not stk_result.get("uncertain"):
            release(order, product)
        return Response({
            "success": False, "message": stk_result.get("message", "Unable to initiate payment."),
        }, status=409 if stk_result.get("uncertain") else 400)

    mpesa_data = stk_result["mpesa"]
    attempt = PaymentAttempt.objects.create(
        payment=payment, phone_number=phone_number, amount=payment.amount,
        request_confirmed=True, provider_environment=environment(),
        business_shortcode=os.getenv("MPESA_SHORTCODE", ""),
        checkout_request_id=mpesa_data["CheckoutRequestID"],
        merchant_request_id=mpesa_data.get("MerchantRequestID") or "",
    )
    payment.phone_number = phone_number
    payment.status = "PROCESSING"
    payment.checkout_request_id = attempt.checkout_request_id
    payment.merchant_request_id = attempt.merchant_request_id
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




@api_view(["POST"])
@permission_classes([AllowAny])
def mpesa_callback(request):
    # A callback is a hint, not trusted proof of success or failure.
    data = request.data
    body = data.get("Body") if isinstance(data, dict) else None
    stk = body.get("stkCallback") if isinstance(body, dict) else None
    if not isinstance(stk, dict):
        return Response({"ResultCode": 1, "ResultDesc": "Invalid callback body"}, status=400)
    checkout = stk.get("CheckoutRequestID")
    code = stk.get("ResultCode")
    if (not isinstance(checkout, str) or not checkout.strip() or type(code) is not int or code < 0
            or not isinstance(stk.get("ResultDesc", ""), str)):
        return Response({"ResultCode": 1, "ResultDesc": "Invalid callback fields"}, status=400)
    receipt, amount = "", None
    if code == 0:
        container = stk.get("CallbackMetadata")
        items = container.get("Item") if isinstance(container, dict) else None
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("Name"), str) for item in items):
            return Response({"ResultCode": 1, "ResultDesc": "Invalid payment metadata"}, status=400)
        metadata = {item["Name"]: item.get("Value") for item in items}
        if len(items) != len(metadata):
            return Response({"ResultCode": 1, "ResultDesc": "Duplicate metadata fields"}, status=400)
        try:
            amount = Decimal(str(metadata.get("Amount")))
        except (InvalidOperation, ValueError):
            amount = Decimal("NaN")
        receipt = metadata.get("MpesaReceiptNumber")
        if not amount.is_finite() or not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 50:
            return Response({"ResultCode": 1, "ResultDesc": "Payment amount or receipt invalid"}, status=400)
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().filter(checkout_request_id=checkout).first()
        if not attempt:
            return Response({"ResultCode": 1, "ResultDesc": "Payment attempt not found"}, status=404)
        if code == 0 and (amount <= 0 or amount != attempt.amount):
            return Response({"ResultCode": 1, "ResultDesc": "Payment amount or receipt invalid"}, status=400)
        if attempt.callback_received_at and (attempt.callback_receipt_number or code != 0):
            return Response({"ResultCode": 0, "ResultDesc": "Already recorded; verification queued or completed"})
        if attempt.merchant_request_id and stk.get("MerchantRequestID", attempt.merchant_request_id) != attempt.merchant_request_id:
            return Response({"ResultCode": 1, "ResultDesc": "Merchant request mismatch"}, status=400)
        attempt.callback_received_at = timezone.now()
        attempt.callback_result_code = str(code)
        attempt.callback_amount = amount
        attempt.callback_receipt_number = receipt.strip()
        if not attempt.verified_at:
            attempt.verification_status = "PENDING"
        if attempt.next_check_at is None:
            attempt.next_check_at = timezone.now()
        attempt.save()
    # A late receipt is reported metadata only, never payment proof. This update
    # happens after releasing the attempt lock to preserve settlement lock order.
    if code == 0:
        Payment.objects.filter(pk=attempt.payment_id, checkout_request_id=checkout,
                               verified_at__isnull=False, mpesa_receipt_number="").update(mpesa_receipt_number=receipt.strip())
    return Response({"ResultCode": 0, "ResultDesc": "Recorded; awaiting independent verification"})
