from .common_imports import *


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))

    if digits.startswith("254") and len(digits) == 12:
        return digits

    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]

    if len(digits) == 9 and digits[0] in ("7", "1"):
        return "254" + digits

    return None


def json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def error_response(message, status=400):
    return JsonResponse({"success": False, "message": message}, status=status)


def order_payload(order):
    payment = getattr(order, "payment", None)
    dispense = getattr(order, "dispense_request", None)

    return {
        "reference": order.reference,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "unit_price": str(order.unit_price),
        "quantity": order.quantity,
        "total_amount": str(order.total_amount),
        "customer_phone": order.customer_phone,
        "status": order.status,
        "created_at": order.created_at.isoformat(),
        "payment": {
            "status": payment.status if payment else None,
            "method": payment.method if payment else None,
            "receipt_number": (
                payment.mpesa_receipt_number if payment else None
            ),
            "result_description": (
                payment.result_description if payment else None
            ),
        },
        "dispense": {
            "status": dispense.status if dispense else None,
            "machine_slot": dispense.machine_slot if dispense else None,
            "collected_at": (
                dispense.collected_at.isoformat()
                if dispense and dispense.collected_at
                else None
            ),
        },
    }