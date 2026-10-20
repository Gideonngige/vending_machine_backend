import base64
import logging
import os
from zoneinfo import ZoneInfo

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)


def environment():
    return "production" if os.getenv("MPESA_ENVIRONMENT", "").lower() == "production" else "sandbox"


def stk_credentials(shortcode):
    timestamp = timezone.now().astimezone(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{os.getenv('MPESA_PASSKEY')}{timestamp}".encode()).decode()
    return timestamp, password


def get_daraja_urls():
    """Return URLs for the configured Daraja environment."""
    if os.environ.get("MPESA_ENVIRONMENT", "").lower() == "production":
        base_url = "https://api.safaricom.co.ke"
    else:
        base_url = "https://sandbox.safaricom.co.ke"

    return {
        "query_url": f"{base_url}/mpesa/stkpushquery/v1/query",
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
            timeout=(5, 10),
        )
        response.raise_for_status()

        data = response.json()
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            logger.error("Daraja response did not contain an access token.")

        return token

    except (requests.RequestException, ValueError):
        logger.exception("Daraja access token request failed.")
        return None


def _code(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isdecimal() and len(value) <= 10:
        return str(int(value))
    return None


def query_stk(attempt):
    """Query only locally recorded, server-initiated checkout requests over TLS."""
    from django.conf import settings
    if not attempt.checkout_request_id or not attempt.request_confirmed:
        return {"state": "MANUAL_REVIEW", "message": "No confirmed originating checkout request; operator investigation required."}
    if attempt.provider_environment != environment() or attempt.business_shortcode != os.getenv("MPESA_SHORTCODE", ""):
        return {"state": "MANUAL_REVIEW", "message": "Originating M-Pesa environment or shortcode differs from configuration."}
    if not all(os.getenv(key) for key in ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE", "MPESA_PASSKEY")):
        return {"state": "ERROR", "message": "M-Pesa verification credentials are not configured."}
    token = get_access_token()
    if not token:
        return {"state": "ERROR", "message": "Could not authenticate the provider status query."}
    timestamp, password = stk_credentials(attempt.business_shortcode)
    try:
        response = requests.post(
            get_daraja_urls()["query_url"],
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"BusinessShortCode": attempt.business_shortcode, "Password": password,
                  "Timestamp": timestamp, "CheckoutRequestID": attempt.checkout_request_id},
            timeout=(5, 15),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("M-Pesa status query unavailable.")
        return {"state": "ERROR", "message": "Provider status query unavailable; retry later."}
    if not isinstance(data, dict) or _code(data.get("ResponseCode")) != "0":
        return {"state": "ERROR", "message": "Provider did not accept the status query."}
    if data.get("CheckoutRequestID") != attempt.checkout_request_id or (
        attempt.merchant_request_id and data.get("MerchantRequestID") != attempt.merchant_request_id
    ):
        return {"state": "ERROR", "message": "Provider query identifiers did not match the originating request."}
    code = _code(data.get("ResultCode"))
    # ResponseCode acknowledges the query, not the customer's payment.
    if code == "0":
        return {"state": "SUCCESS", "code": code, "message": "Provider confirmed payment success."}
    if code in settings.MPESA_TERMINAL_FAILURE_CODES:
        return {"state": "FAILED", "code": code, "message": "Provider confirmed an unsuccessful payment."}
    return {"state": "PENDING", "code": code or "", "message": "Payment outcome is not confirmed; provider recheck required."}
