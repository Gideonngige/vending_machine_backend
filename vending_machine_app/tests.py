from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from .models import DispenseRequest, Order, Payment, Product


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
