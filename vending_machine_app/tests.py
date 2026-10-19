from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from .models import DispenseRequest, Order, Payment, PaymentAttempt, Product


class PaymentCallbackTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Espresso", price=Decimal("100"), machine_slot=1,
            stock_quantity=10,
        )
        self.order = Order.objects.create(
            product=self.product, quantity=1, customer_phone="254712345678",
        )
        self.payment = Payment.objects.create(
            order=self.order, amount=self.order.total_amount,
            phone_number=self.order.customer_phone,
            checkout_request_id="checkout-test", status=Payment.Status.PROCESSING,
        )
        self.dispense = DispenseRequest.objects.create(
            order=self.order, machine_slot=1,
        )
        self.attempt = PaymentAttempt.objects.create(
            payment=self.payment, phone_number=self.payment.phone_number,
            amount=self.payment.amount, checkout_request_id="checkout-test",
        )
        self.payload = {"Body": {"stkCallback": {
            "CheckoutRequestID": "checkout-test", "ResultCode": 0,
            "ResultDesc": "Success", "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": 100},
                {"Name": "MpesaReceiptNumber", "Value": "TESTRECEIPT"},
            ]},
        }}}

    def callback(self):
        return self.client.post(
            "/api/v1/payments/callback/", self.payload,
            content_type="application/json",
        )

    def test_success_records_receipt_without_claiming_dispense_completion(self):
        self.assertEqual(self.callback().status_code, 200)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.dispense.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.payment.mpesa_receipt_number, "TESTRECEIPT")
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.dispense.status, DispenseRequest.Status.PENDING)
        self.assertEqual(self.product.stock_quantity, 9)

    def test_duplicate_callback_does_not_reduce_stock_twice(self):
        self.callback()
        self.callback()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)

    def test_failure_keeps_order_unpaid_and_stock_unchanged(self):
        self.payload["Body"]["stkCallback"]["ResultCode"] = 1032
        self.callback()
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.assertEqual(self.product.stock_quantity, 10)

    def test_database_failure_returns_error_and_rolls_back(self):
        with patch.object(Product, "save", side_effect=RuntimeError("test error")):
            with self.assertLogs("vending_machine_app.api_views.payment", level="ERROR"):
                response = self.callback()
        self.assertEqual(response.status_code, 500)
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PROCESSING)
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_invalid_success_metadata_leaves_records_unchanged(self):
        invalid_items = [
            [],
            [{"Name": "Amount", "Value": 99},
             {"Name": "MpesaReceiptNumber", "Value": "TEST"}],
            [{"Name": "Amount", "Value": "NaN"},
             {"Name": "MpesaReceiptNumber", "Value": "TEST"}],
            [{"Name": "Amount", "Value": 100}],
            [{"Name": "Amount", "Value": 100},
             {"Name": "MpesaReceiptNumber", "Value": " "}],
            [None],
            [{"Name": "Amount", "Value": 100},
             {"Name": "Amount", "Value": 100},
             {"Name": "MpesaReceiptNumber", "Value": "TEST"}],
        ]
        for items in invalid_items:
            with self.subTest(items=items):
                self.payload["Body"]["stkCallback"]["CallbackMetadata"]["Item"] = items
                self.assertEqual(self.callback().status_code, 400)
                self.payment.refresh_from_db()
                self.order.refresh_from_db()
                self.product.refresh_from_db()
                self.assertEqual(self.payment.status, Payment.Status.PROCESSING)
                self.assertEqual(self.order.status, Order.Status.PENDING)
                self.assertEqual(self.product.stock_quantity, 10)

    def test_malformed_callback_is_rejected(self):
        for payload in [{}, {"Body": None}, {"Body": {"stkCallback": []}},
                        {"Body": {"stkCallback": {"CheckoutRequestID": "checkout-test"}}}]:
            with self.subTest(payload=payload):
                self.payload = payload
                self.assertEqual(self.callback().status_code, 400)

    def test_late_failure_does_not_fail_newer_attempt(self):
        PaymentAttempt.objects.create(
            payment=self.payment, phone_number=self.payment.phone_number,
            amount=self.payment.amount, checkout_request_id="newer",
        )
        self.payload["Body"]["stkCallback"]["ResultCode"] = 1032
        self.callback()
        self.payment.refresh_from_db()
        self.attempt.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PROCESSING)
        self.assertEqual(self.attempt.status, PaymentAttempt.Status.FAILED)

    def test_two_successful_attempts_preserve_receipts_and_decrement_once(self):
        self.callback()
        second = PaymentAttempt.objects.create(
            payment=self.payment, phone_number=self.payment.phone_number,
            amount=self.payment.amount, checkout_request_id="second",
        )
        stk = self.payload["Body"]["stkCallback"]
        stk["CheckoutRequestID"] = "second"
        stk["CallbackMetadata"]["Item"][1]["Value"] = "SECONDRECEIPT"
        self.callback()
        second.refresh_from_db()
        self.payment.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(second.mpesa_receipt_number, "SECONDRECEIPT")
        self.assertEqual(self.payment.mpesa_receipt_number, "TESTRECEIPT")
        self.assertEqual(self.product.stock_quantity, 9)

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_pending_attempt_blocks_another_stk_request(self, send):
        response = self.client.post(
            f"/api/v1/payments/{self.order.reference}/stk-push/",
            {"phone_number": self.payment.phone_number}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_retry_keeps_previous_attempt(self, send):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save()
        send.return_value = {"success": True, "mpesa": {"CheckoutRequestID": "retry"}}
        response = self.client.post(
            f"/api/v1/payments/{self.order.reference}/stk-push/",
            {"phone_number": self.payment.phone_number}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.payment.attempts.count(), 2)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.checkout_request_id, "checkout-test")
        # The old attempt can still succeed after a retry starts.
        self.assertEqual(self.callback().status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.SUCCESS)

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_uncertain_request_blocks_retry(self, send):
        self.attempt.status = PaymentAttempt.Status.FAILED
        self.attempt.save()
        send.return_value = {"success": False, "uncertain": True, "message": "Timeout"}
        url = f"/api/v1/payments/{self.order.reference}/stk-push/"
        body = {"phone_number": self.payment.phone_number}
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 409)
        self.assertTrue(self.payment.attempts.filter(status="UNKNOWN").exists())
        self.client.post(url, body, content_type="application/json")
        self.assertEqual(send.call_count, 1)


class PaymentAttemptMigrationTests(TransactionTestCase):
    def test_existing_checkout_is_preserved(self):
        old_target = [("vending_machine_app", "0001_initial")]
        new_target = [("vending_machine_app", "0002_paymentattempt")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            ProductModel = apps.get_model("vending_machine_app", "Product")
            OrderModel = apps.get_model("vending_machine_app", "Order")
            PaymentModel = apps.get_model("vending_machine_app", "Payment")
            product = ProductModel.objects.create(name="Coffee", price=100, machine_slot=1)
            order = OrderModel.objects.create(
                product=product, reference="LEGACY", product_name="Coffee",
                unit_price=100, total_amount=100, customer_phone="254712345678",
            )
            payment = PaymentModel.objects.create(
                order=order, phone_number="254712345678", amount=100,
                checkout_request_id="legacy-checkout", status="SUCCESS",
                mpesa_receipt_number="LEGACYRECEIPT",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            apps = executor.loader.project_state(new_target).apps
            attempt = apps.get_model("vending_machine_app", "PaymentAttempt").objects.get(payment_id=payment.pk)
            self.assertEqual(attempt.checkout_request_id, "legacy-checkout")
            self.assertEqual(attempt.mpesa_receipt_number, "LEGACYRECEIPT")
            self.assertEqual(attempt.status, "SUCCESS")
        finally:
            MigrationExecutor(connection).migrate(new_target)
