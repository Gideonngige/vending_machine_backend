import json
import re

from django.http import JsonResponse
from django.utils import timezone
import base64
import datetime
import logging
import requests

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

logger = logging.getLogger(__name__)



def get_daraja_configuration(tenant):
    config = (
        DarajaConfiguration.objects
        .filter(
            tenant=tenant,
            is_active=True
        )
        .first()
    )

    return config



# ============================================================
# GET DARAJA URLS
# ============================================================

def get_daraja_urls(environment):
    if environment == "production":
        return {
            "access_token_url":
                "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",

            "stk_push_url":
                "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        }

    return {
        "access_token_url":
            "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",

        "stk_push_url":
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
    }


# ============================================================
# GET ACCESS TOKEN
# ============================================================

def get_access_token(daraja_config):
    try:
        urls = get_daraja_urls(
            daraja_config.environment
        )

        response = requests.get(
            urls["access_token_url"],
            auth=(
                daraja_config.consumer_key,
                daraja_config.consumer_secret,
            ),
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        access_token = data.get(
            "access_token"
        )

        if not access_token:
            return None

        return access_token

    except requests.RequestException as error:
        print(
            "DARAJA ACCESS TOKEN ERROR:",
            error
        )

        return None

    except Exception as error:
        print(
            "ACCESS TOKEN ERROR:",
            error
        )

        return None


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))

    if digits.startswith("254") and len(digits) == 12:
        return digits

    if digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]

    if len(digits) == 9 and digits[0] in ("7", "1"):
        return "254" + digits

    return None







# ============================================================
# M-PESA STK PUSH - COFFEE VENDING SYSTEM
# ============================================================

def lipa_na_mpesa(
    daraja_config,
    phone_number,
    amount,
    order_reference
):
    try:
        # ----------------------------------------------------
        # 1. GET ACCESS TOKEN
        # ----------------------------------------------------

        access_token = get_access_token(
            daraja_config
        )

        if not access_token:
            return {
                "success": False,
                "message": "Failed to get M-Pesa access token."
            }

        # ----------------------------------------------------
        # 2. VALIDATE PHONE NUMBER
        # ----------------------------------------------------

        phone_number = normalize_mpesa_phone(
            phone_number
        )

        if not phone_number:
            return {
                "success": False,
                "message": "Invalid M-Pesa phone number."
            }

        # ----------------------------------------------------
        # 3. VALIDATE PAYMENT AMOUNT
        # ----------------------------------------------------

        try:
            amount = Decimal(str(amount))

            if not amount.is_finite() or amount <= 0:
                raise ValueError("Invalid amount")

            # M-Pesa STK Push requires a whole KSh amount.
            amount = int(
                amount.quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP
                )
            )

            if amount < 1:
                raise ValueError("Amount must be at least KSh 1")

        except (InvalidOperation, ValueError, TypeError):
            return {
                "success": False,
                "message": "Invalid coffee payment amount."
            }

        # ----------------------------------------------------
        # 4. GENERATE TIMESTAMP
        # ----------------------------------------------------

        timestamp = (
            datetime.datetime
            .now(datetime.timezone.utc)
            .astimezone(
                datetime.timezone(
                    datetime.timedelta(hours=3)
                )
            )
            .strftime("%Y%m%d%H%M%S")
        )

        # ----------------------------------------------------
        # 5. GENERATE M-PESA PASSWORD
        # ----------------------------------------------------

        shortcode = str(
            daraja_config.short_code
        )

        password_string = (
            f"{shortcode}"
            f"{daraja_config.passkey}"
            f"{timestamp}"
        )

        password = base64.b64encode(
            password_string.encode("utf-8")
        ).decode("utf-8")

        # ----------------------------------------------------
        # 6. GET DARAJA API URLS
        # ----------------------------------------------------

        urls = get_daraja_urls(
            daraja_config.environment
        )

        # ----------------------------------------------------
        # 7. REQUEST HEADERS
        # ----------------------------------------------------

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # ----------------------------------------------------
        # 8. STK PUSH PAYLOAD
        # ----------------------------------------------------

        payload = {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,

            "TransactionType": "CustomerPayBillOnline",

            "Amount": amount,

            "PartyA": phone_number,
            "PartyB": shortcode,
            "PhoneNumber": phone_number,

            "CallBackURL": daraja_config.callback_url,

            "AccountReference": str(order_reference)[:12],

            "TransactionDesc": "Coffee Vending Payment",
        }

        # ----------------------------------------------------
        # 9. SEND STK PUSH REQUEST
        # ----------------------------------------------------

        response = requests.post(
            urls["stk_push_url"],
            json=payload,
            headers=headers,
            timeout=30,
        )

        # ----------------------------------------------------
        # 10. PROCESS RESPONSE
        # ----------------------------------------------------

        try:
            response_data = response.json()

        except ValueError:
            return {
                "success": False,
                "message": "Invalid response from M-Pesa.",
            }

        if not response.ok:
            return {
                "success": False,
                "message": (
                    response_data.get("errorMessage")
                    or response_data.get("ResponseDescription")
                    or "M-Pesa request failed."
                ),
                "mpesa": response_data,
            }

        # ----------------------------------------------------
        # 11. CHECK IF STK PUSH WAS ACCEPTED
        # ----------------------------------------------------

        if str(response_data.get("ResponseCode")) != "0":
            return {
                "success": False,
                "message": (
                    response_data.get("ResponseDescription")
                    or "M-Pesa rejected the STK Push request."
                ),
                "mpesa": response_data,
            }

        checkout_request_id = response_data.get(
            "CheckoutRequestID"
        )

        if not checkout_request_id:
            return {
                "success": False,
                "message": "M-Pesa did not return a CheckoutRequestID.",
                "mpesa": response_data,
            }

        # ----------------------------------------------------
        # 12. RETURN SUCCESSFUL INITIATION
        # ----------------------------------------------------

        return {
            "success": True,
            "message": "STK Push sent successfully. Awaiting payment.",
            "checkout_request_id": checkout_request_id,
            "merchant_request_id": response_data.get(
                "MerchantRequestID"
            ),
            "response_code": response_data.get(
                "ResponseCode"
            ),
            "mpesa": response_data,
        }

    except requests.exceptions.Timeout:
        logger.warning(
            "M-Pesa STK Push timed out for order %s",
            order_reference
        )

        return {
            "success": False,
            "message": (
                "M-Pesa request timed out. "
                "Check payment status before retrying."
            ),
            "uncertain": True,
        }

    except requests.exceptions.RequestException:
        logger.exception(
            "M-Pesa network error for order %s",
            order_reference
        )

        return {
            "success": False,
            "message": "Unable to communicate with M-Pesa.",
            "uncertain": True,
        }

    except Exception:
        logger.exception(
            "STK Push error for order %s",
            order_reference
        )

        return {
            "success": False,
            "message": "Unable to initiate coffee payment.",
        }
