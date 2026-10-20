import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch, Mock
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Barrier

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.db import close_old_connections, connections
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from .daraja import query_stk
from .models import Machine, Product, Order, Payment, PaymentAttempt, StockReservation
from .reconciliation import reconcile_attempt


class ReconciliationFixture:
    def setUp(self):
        self.automatic_check = patch("vending_machine_app.api_views.payment.reconcile_attempt")
        self.automatic_check.start()
        self.addCleanup(self.automatic_check.stop)
        machine = Machine.objects.get(code="MACHINE-001")
        self.product = Product.objects.create(machine=machine, name="Coffee", price=100, machine_slot=1, stock_quantity=2)
        self.order = Order.objects.create(product=self.product, customer_phone="254712345678")
        self.payment = Payment.objects.create(order=self.order, amount=100, phone_number=self.order.customer_phone,
                                              status="PROCESSING", checkout_request_id="checkout")
        self.attempt = PaymentAttempt.objects.create(payment=self.payment, amount=100, phone_number=self.order.customer_phone,
                                                    checkout_request_id="checkout", merchant_request_id="merchant",
                                                    request_confirmed=True, provider_environment="sandbox", business_shortcode="174379")
        self.hold = StockReservation.objects.create(order=self.order, quantity=1, expires_at=timezone.now() + timedelta(minutes=10))
        self.callback_url = "/api/v1/payments/callback/"
        self.endpoint = f"/api/v1/management/payment-attempts/{self.attempt.pk}/reconcile/"

    def callback(self, code=0):
        return self.client.post(self.callback_url, {"Body": {"stkCallback": {
            "CheckoutRequestID": "checkout", "MerchantRequestID": "merchant", "ResultCode": code,
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 100}, {"Name": "MpesaReceiptNumber", "Value": "REPORTED"}]},
        }}}, content_type="application/json")

    def verify(self, state="SUCCESS", code="0"):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={"state": state, "code": code, "message": "Provider test result"}):
            return reconcile_attempt(self.attempt.pk)



class ReconciliationTests(ReconciliationFixture, TestCase):
    def test_callback_alone_cannot_mark_paid_or_deduct_stock(self):
        self.assertEqual(self.callback().status_code, 200)
        self.payment.refresh_from_db(); self.product.refresh_from_db(); self.attempt.refresh_from_db()
        self.assertEqual(self.payment.status, "PROCESSING")
        self.assertIsNone(self.payment.verified_at)
        self.assertEqual(self.product.stock_quantity, 2)
        self.assertEqual(self.attempt.verification_status, "PENDING")
        self.assertEqual(self.attempt.callback_receipt_number, "REPORTED")

    def test_forged_success_is_overruled_by_provider_failure(self):
        self.callback()
        self.verify("FAILED", "1032")
        self.payment.refresh_from_db(); self.product.refresh_from_db(); self.hold.refresh_from_db()
        self.assertEqual(self.payment.status, "FAILED")
        self.assertEqual(self.product.stock_quantity, 2)
        self.assertEqual(self.hold.status, "RELEASED")

    def test_forged_failure_cannot_override_provider_success(self):
        self.callback(1032)
        self.verify()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "SUCCESS")
        self.assertIsNotNone(self.payment.verified_at)
        self.assertEqual(self.payment.mpesa_receipt_number, "")

    def test_missing_callback_can_be_recovered_without_inventing_receipt(self):
        self.verify()
        self.payment.refresh_from_db(); self.product.refresh_from_db(); self.hold.refresh_from_db()
        self.assertEqual(self.payment.status, "SUCCESS")
        self.assertEqual(self.payment.mpesa_receipt_number, "")
        self.assertEqual(self.product.stock_quantity, 1)
        self.assertEqual(self.hold.status, "CONSUMED")

    def test_provider_outage_leaves_order_unpaid_and_stock_held(self):
        self.callback()
        self.verify("ERROR", "")
        self.payment.refresh_from_db(); self.hold.refresh_from_db(); self.attempt.refresh_from_db()
        self.assertEqual(self.payment.status, "PROCESSING")
        self.assertEqual(self.hold.status, "ACTIVE")
        self.assertEqual(self.attempt.status, "UNKNOWN")
        self.assertEqual(self.attempt.verification_status, "ERROR")

    def test_repeated_verification_does_not_query_or_settle_twice(self):
        self.verify()
        with patch("vending_machine_app.reconciliation.query_stk") as query:
            self.assertEqual(reconcile_attempt(self.attempt.pk)["outcome"], "ALREADY_VERIFIED")
            query.assert_not_called()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 1)

    def test_cooldown_and_active_lease_defer_requests(self):
        for fields in ({"next_check_at": timezone.now() + timedelta(minutes=1)},
                       {"next_check_at": None, "check_token": uuid4(), "check_started_at": timezone.now()}):
            PaymentAttempt.objects.filter(pk=self.attempt.pk).update(**fields)
            with patch("vending_machine_app.reconciliation.query_stk") as query:
                self.assertEqual(reconcile_attempt(self.attempt.pk)["outcome"], "DEFERRED")
                query.assert_not_called()

    def test_expired_worker_lease_can_be_recovered(self):
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(check_token=uuid4(), check_started_at=timezone.now() - timedelta(minutes=3))
        self.assertEqual(self.verify()["outcome"], "SUCCESS")

    def test_superseded_query_cannot_settle_payment(self):
        def provider(attempt):
            PaymentAttempt.objects.filter(pk=attempt.pk).update(check_token=uuid4())
            return {"state": "SUCCESS", "code": "0", "message": "Provider success"}
        with patch("vending_machine_app.reconciliation.query_stk", side_effect=provider):
            self.assertEqual(reconcile_attempt(self.attempt.pk)["outcome"], "SUPERSEDED")
        self.payment.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.payment.status, "PROCESSING")
        self.assertEqual(self.product.stock_quantity, 2)

    def test_order_amount_mismatch_requires_manual_review(self):
        self.order.total_amount = 200
        self.order.save()
        self.verify()
        self.payment.refresh_from_db(); self.attempt.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.payment.status, "PROCESSING")
        self.assertEqual(self.attempt.verification_status, "MANUAL_REVIEW")
        self.assertEqual(self.product.stock_quantity, 2)

    def test_verifying_already_paid_record_does_not_deduct_stock_again(self):
        self.payment.status = "SUCCESS"
        self.payment.save()
        self.verify()
        self.payment.refresh_from_db(); self.product.refresh_from_db()
        self.assertIsNotNone(self.payment.verified_at)
        self.assertEqual(self.product.stock_quantity, 2)

    def test_verification_endpoint_is_staff_only(self):
        with patch("vending_machine_app.reconciliation.query_stk") as query:
            self.assertEqual(self.client.post(self.endpoint).status_code, 401)
            user = get_user_model().objects.create_user(username="customer")
            self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(user)}"
            self.assertEqual(self.client.post(self.endpoint).status_code, 403)
            query.assert_not_called()
        user.is_staff = True; user.save()
        with patch("vending_machine_app.reconciliation.query_stk", return_value={"state": "PENDING", "message": "Pending"}):
            self.assertEqual(self.client.post(self.endpoint).status_code, 200)

    @patch("vending_machine_app.management.commands.reconcile_payments.close_old_connections")
    def test_worker_checks_due_attempts_and_respects_cooldown(self, close_connections):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={"state": "PENDING", "message": "Pending"}) as query:
            call_command("reconcile_payments", stdout=StringIO())
            call_command("reconcile_payments", stdout=StringIO())
            self.assertEqual(query.call_count, 1)

    def test_late_callback_can_add_reported_receipt_after_verification(self):
        self.verify()
        self.assertEqual(self.callback().status_code, 200)
        self.attempt.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.attempt.verification_status, "VERIFIED")
        self.assertEqual(self.attempt.callback_receipt_number, "REPORTED")
        self.assertEqual(self.product.stock_quantity, 1)

    @override_settings(DEBUG=True)
    def test_collection_rejects_unverified_payment(self):
        self.order.status = "READY"; self.order.save()
        self.payment.status = "SUCCESS"; self.payment.save()
        from .models import DispenseRequest
        DispenseRequest.objects.create(order=self.order, machine_slot=1, status="READY")
        response = self.client.post(f"/api/v1/orders/{self.order.reference}/collect/", {}, content_type="application/json")
        self.assertEqual(response.status_code, 409)


@patch.dict(os.environ, {"MPESA_ENVIRONMENT": "sandbox", "MPESA_SHORTCODE": "174379",
                        "MPESA_PASSKEY": "test-passkey", "MPESA_CONSUMER_KEY": "test-key", "MPESA_CONSUMER_SECRET": "test-secret"})
class DarajaQueryTests(ReconciliationFixture, TestCase):
    def provider_data(self, **changes):
        return {"ResponseCode": "0", "CheckoutRequestID": "checkout", "MerchantRequestID": "merchant", "ResultCode": "0", **changes}

    def query_response(self, data):
        with patch("vending_machine_app.daraja.get_access_token", return_value="test-token"), patch("vending_machine_app.daraja.requests.post") as post:
            post.return_value = Mock(json=Mock(return_value=data))
            result = query_stk(self.attempt)
            self.assertEqual(post.call_args.kwargs["json"]["CheckoutRequestID"], "checkout")
            self.assertEqual(post.call_args.kwargs["json"]["BusinessShortCode"], "174379")
            self.assertTrue(post.call_args.args[0].endswith("/mpesa/stkpushquery/v1/query"))
            return result

    def test_adapter_validates_result_and_identifiers(self):
        for changes, state in [({}, "SUCCESS"), ({"ResultCode": "1032"}, "FAILED"),
                               ({"ResultCode": "4999"}, "PENDING"), ({"ResultCode": None}, "PENDING"),
                               ({"ResponseCode": "1"}, "ERROR"), ({"CheckoutRequestID": "wrong"}, "ERROR"),
                               ({"MerchantRequestID": "wrong"}, "ERROR"), ({"ResultCode": False}, "PENDING")]:
            with self.subTest(changes=changes):
                self.assertEqual(self.query_response(self.provider_data(**changes))["state"], state)

    def test_unbound_or_unknown_checkout_never_queries_provider(self):
        for fields in ({"checkout_request_id": None}, {"request_confirmed": False}, {"provider_environment": "production"}, {"business_shortcode": "wrong"}):
            candidate = PaymentAttempt.objects.get(pk=self.attempt.pk)
            for key, value in fields.items(): setattr(candidate, key, value)
            with patch("vending_machine_app.daraja.requests.post") as post:
                self.assertEqual(query_stk(candidate)["state"], "MANUAL_REVIEW")
                post.assert_not_called()

    def test_query_network_and_json_errors_never_confirm_payment(self):
        with patch("vending_machine_app.daraja.get_access_token", return_value="token"), patch("vending_machine_app.daraja.requests.post", side_effect=requests.Timeout):
            self.assertEqual(query_stk(self.attempt)["state"], "ERROR")
        with patch("vending_machine_app.daraja.get_access_token", return_value="token"), patch("vending_machine_app.daraja.requests.post") as post:
            post.return_value = Mock(json=Mock(side_effect=ValueError("invalid JSON")))
            self.assertEqual(query_stk(self.attempt)["state"], "ERROR")

    def test_missing_credentials_do_not_call_provider(self):
        with patch.dict(os.environ, {"MPESA_CONSUMER_KEY": ""}), patch("vending_machine_app.daraja.get_access_token") as token:
            self.assertEqual(query_stk(self.attempt)["state"], "ERROR")
            token.assert_not_called()


class ConcurrentVerificationTests(TransactionTestCase):
    def setUp(self):
        machine, _ = Machine.objects.get_or_create(code="MACHINE-001", defaults={"name": "Main"})
        self.product = Product.objects.create(machine=machine, name="Coffee", price=100, machine_slot=1, stock_quantity=2)
        order = Order.objects.create(product=self.product, customer_phone="254712345678")
        self.payment = Payment.objects.create(order=order, amount=100, phone_number=order.customer_phone)
        self.attempt = PaymentAttempt.objects.create(payment=self.payment, amount=100, phone_number=order.customer_phone, checkout_request_id="checkout")

    def run_check(self, pk):
        close_old_connections()
        try:
            return reconcile_attempt(pk)
        finally:
            connections.close_all()

    @skipUnlessDBFeature("has_select_for_update")
    def test_workers_share_a_lease_for_one_attempt(self):
        started, finish = Event(), Event()

        def provider(attempt):
            started.set()
            if not finish.wait(timeout=10):
                raise RuntimeError("Test provider timed out")
            return {"state": "SUCCESS", "code": "0", "message": "Provider success"}

        with patch("vending_machine_app.reconciliation.query_stk", side_effect=provider) as query:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.run_check, self.attempt.pk)
                try:
                    self.assertTrue(started.wait(timeout=10))
                    second = pool.submit(self.run_check, self.attempt.pk)
                    self.assertEqual(second.result(timeout=10)["outcome"], "DEFERRED")
                finally:
                    finish.set()
                self.assertEqual(first.result(timeout=10)["outcome"], "SUCCESS")
            self.assertEqual(query.call_count, 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_successful_attempts_settle_inventory_once(self):
        second = PaymentAttempt.objects.create(payment=self.payment, amount=100, phone_number=self.payment.phone_number, checkout_request_id="second")
        barrier = Barrier(2)

        def provider(attempt):
            barrier.wait(timeout=10)
            return {"state": "SUCCESS", "code": "0", "message": "Provider success"}

        with patch("vending_machine_app.reconciliation.query_stk", side_effect=provider):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(self.run_check, [self.attempt.pk, second.pk]))
        self.assertEqual([r["outcome"] for r in results], ["SUCCESS", "SUCCESS"])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 1)
        self.assertEqual(self.payment.attempts.filter(verification_status="VERIFIED").count(), 2)


class AutomaticCallbackVerificationTests(ReconciliationFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.automatic_check.stop()

    def test_callback_queries_provider_and_settles_without_worker(self):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={
            "state": "SUCCESS", "code": "0", "message": "Verified by provider",
        }) as query:
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(self.callback().status_code, 200)
        self.payment.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(query.call_count, 1)
        self.assertEqual(self.payment.status, "SUCCESS")
        self.assertIsNotNone(self.payment.verified_at)
        self.assertEqual(self.product.stock_quantity, 1)

    def test_callback_success_hint_cannot_overrule_provider_failure(self):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={
            "state": "FAILED", "code": "1032", "message": "Provider confirmed cancellation",
        }):
            self.assertEqual(self.callback().status_code, 200)
        self.payment.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.payment.status, "FAILED")
        self.assertEqual(self.product.stock_quantity, 2)

    def test_outage_is_acknowledged_and_duplicates_respect_cooldown(self):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={
            "state": "ERROR", "message": "Provider unavailable",
        }) as query:
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(query.call_count, 1)
        self.payment.refresh_from_db(); self.attempt.refresh_from_db()
        self.assertNotEqual(self.payment.status, "SUCCESS")
        self.assertEqual(self.attempt.verification_status, "ERROR")
        self.assertIsNotNone(self.attempt.callback_received_at)
        self.assertIsNotNone(self.attempt.next_check_at)

    def test_duplicate_callback_can_recover_when_next_check_is_due(self):
        with patch("vending_machine_app.reconciliation.query_stk", return_value={"state": "PENDING", "message": "Pending"}):
            self.callback()
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(next_check_at=timezone.now()-timedelta(seconds=1))
        with patch("vending_machine_app.reconciliation.query_stk", return_value={"state": "SUCCESS", "code": "0", "message": "Verified"}):
            self.callback()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "SUCCESS")

    def test_unexpected_verification_error_keeps_callback_for_recovery(self):
        with patch("vending_machine_app.api_views.payment.reconcile_attempt", side_effect=RuntimeError("private internals")):
            response = self.callback()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private internals", str(response.content))
        self.attempt.refresh_from_db()
        self.assertIsNotNone(self.attempt.callback_received_at)
        self.assertIsNone(self.attempt.verified_at)
