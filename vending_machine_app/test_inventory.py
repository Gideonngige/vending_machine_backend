from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from .inventory import available_quantity
from .reconciliation import reconcile_attempt
from .models import Machine, Product, Order, Payment, PaymentAttempt, StockReservation


class ReservationTests(TestCase):
    def setUp(self):
        self.automatic_check = patch("vending_machine_app.api_views.payment.reconcile_attempt")
        self.automatic_check.start()
        self.addCleanup(self.automatic_check.stop)
        self.machine = Machine.objects.get(code="MACHINE-001")
        self.product = Product.objects.create(machine=self.machine, name="Coffee", price=100, machine_slot=1, stock_quantity=1)

    def create(self):
        return self.client.post("/api/v1/orders/", {"product_id": self.product.pk, "phone": "0712345678"}, content_type="application/json")

    def expire(self, order):
        StockReservation.objects.filter(order=order).update(expires_at=timezone.now() - timedelta(seconds=1))

    def callback(self, order, code=0):
        payment = order.payment
        attempt = PaymentAttempt.objects.create(payment=payment, phone_number=order.customer_phone,
                                               amount=payment.amount, checkout_request_id=f"checkout-{order.pk}")
        response = self.client.post("/api/v1/payments/callback/", {"Body": {"stkCallback": {
            "CheckoutRequestID": attempt.checkout_request_id, "ResultCode": code, "ResultDesc": "Test result",
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": int(payment.amount)}, {"Name": "MpesaReceiptNumber", "Value": f"R-{order.pk}"}]},
        }}}, content_type="application/json")
        if response.status_code == 200:
            with patch("vending_machine_app.reconciliation.query_stk", return_value={
                "state": "SUCCESS" if code == 0 else "FAILED", "code": str(code), "message": "Provider test result",
            }):
                reconcile_attempt(attempt.pk)
        return response

    def new_order(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        return Order.objects.get(reference=response.json()["order"]["reference"])

    def test_order_holds_stock_without_reducing_physical_inventory(self):
        order = self.new_order()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 1)
        self.assertEqual(order.stock_reservation.quantity, 1)
        self.assertEqual(available_quantity(self.product), 0)
        menu = self.client.get("/api/v1/products/").json()["products"][0]
        self.assertFalse(menu["available"])
        self.assertEqual(menu["available_quantity"], 0)
        self.assertEqual(self.create().status_code, 409)

    def test_reservation_tracks_order_quantity(self):
        self.product.stock_quantity = 5
        self.product.save()
        response = self.client.post("/api/v1/orders/", {"product_id": self.product.pk, "quantity": 3, "phone": "0712345678"}, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(reference=response.json()["order"]["reference"])
        self.assertEqual(order.stock_reservation.quantity, 3)
        self.assertEqual(available_quantity(self.product), 2)
        self.assertEqual(self.callback(order).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 2)
        self.assertEqual(available_quantity(self.product), 2)

    def test_expired_holds_release_availability_without_a_scheduled_job(self):
        order = self.new_order()
        self.expire(order)
        self.assertEqual(available_quantity(self.product), 1)
        self.assertEqual(self.client.get(f"/api/v1/orders/{order.reference}/").json()["order"]["reservation"]["status"], "EXPIRED")
        self.assertEqual(self.create().status_code, 201)
        first, second = StringIO(), StringIO()
        call_command("expire_reservations", stdout=first)
        call_command("expire_reservations", stdout=second)
        self.assertIn("Expired 1", first.getvalue())
        self.assertIn("Expired 0", second.getvalue())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 1)

    def test_cancellation_releases_hold_and_is_idempotent(self):
        order = self.new_order()
        url = f"/api/v1/orders/{order.reference}/cancel/"
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(available_quantity(self.product), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, "CANCELLED")

    def test_cannot_cancel_processing_payment_or_paid_order(self):
        order = self.new_order()
        attempt = PaymentAttempt.objects.create(payment=order.payment, phone_number=order.customer_phone, amount=100)
        for status in ("PROCESSING", "UNKNOWN"):
            attempt.status = status
            attempt.save()
            self.assertEqual(self.client.post(f"/api/v1/orders/{order.reference}/cancel/").status_code, 409)
        order.payment.status = "SUCCESS"
        order.payment.save()
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.reference}/cancel/").status_code, 409)

    def test_success_consumes_hold_and_duplicate_does_not_consume_twice(self):
        order = self.new_order()
        self.assertEqual(self.callback(order).status_code, 200)
        attempt = order.payment.attempts.get()
        payload = {"Body": {"stkCallback": {"CheckoutRequestID": attempt.checkout_request_id, "ResultCode": 0, "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 100}, {"Name": "MpesaReceiptNumber", "Value": f"R-{order.pk}"}]}}}}
        self.assertEqual(self.client.post("/api/v1/payments/callback/", payload, content_type="application/json").status_code, 200)
        self.product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 0)
        self.assertEqual(order.stock_reservation.status, "CONSUMED")
        self.assertEqual(order.status, "PAID")

    def test_failed_payment_releases_hold(self):
        order = self.new_order()
        self.callback(order, code=1032)
        self.assertEqual(available_quantity(self.product), 1)
        order.refresh_from_db()
        self.assertEqual(order.stock_reservation.status, "RELEASED")

    def test_late_success_uses_free_stock(self):
        order = self.new_order()
        self.expire(order)
        self.callback(order)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, "PAID")
        self.assertEqual(self.product.stock_quantity, 0)

    def test_late_payment_cannot_take_another_orders_hold(self):
        late = self.new_order()
        self.expire(late)
        current = self.new_order()
        self.callback(late)
        late.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(late.payment.status, "SUCCESS")
        self.assertEqual(late.status, "FAILED")
        self.assertEqual(late.dispense_request.status, "FAILED")
        self.assertEqual(self.product.stock_quantity, 1)
        self.assertEqual(current.stock_reservation.status, "ACTIVE")
        self.assertEqual(available_quantity(self.product), 0)

    def test_late_payment_for_cancelled_order_requires_review(self):
        order = self.new_order()
        self.client.post(f"/api/v1/orders/{order.reference}/cancel/")
        self.callback(order)
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.payment.status, "SUCCESS")
        self.assertEqual(order.status, "FAILED")
        self.assertEqual(self.product.stock_quantity, 1)

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_expired_order_cannot_initiate_payment_if_stock_is_held(self, send):
        expired = self.new_order()
        self.expire(expired)
        self.new_order()
        response = self.client.post(f"/api/v1/payments/{expired.reference}/stk-push/", {"phone_number": "0712345678"}, content_type="application/json")
        self.assertEqual(response.status_code, 409)
        send.assert_not_called()

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_failed_retry_reacquires_hold_and_unknown_outcome_keeps_it(self, send):
        order = self.new_order()
        self.callback(order, code=1032)
        send.return_value = {"success": False, "uncertain": True, "message": "Timeout"}
        self.client.post(f"/api/v1/payments/{order.reference}/stk-push/", {"phone_number": "0712345678"}, content_type="application/json")
        self.assertEqual(available_quantity(self.product), 0)
        order.refresh_from_db()
        self.assertEqual(order.stock_reservation.status, "ACTIVE")

    @patch("vending_machine_app.api_views.payment.lipa_na_mpesa")
    def test_rejected_stk_request_releases_hold(self, send):
        order = self.new_order()
        send.return_value = {"success": False, "message": "Rejected"}
        self.client.post(f"/api/v1/payments/{order.reference}/stk-push/", {"phone_number": "0712345678"}, content_type="application/json")
        self.assertEqual(available_quantity(self.product), 1)

    def test_stock_editor_cannot_reduce_inventory_below_held_units(self):
        self.new_order()
        self.product.refresh_from_db()
        staff = get_user_model().objects.create_user(username="owner", is_staff=True)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(staff)}"
        response = self.client.patch(f"/api/v1/management/products/{self.product.pk}/", {
            "stock_quantity": 0, "expected_updated_at": self.product.updated_at.isoformat(),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)

    @patch("vending_machine_app.api_views.order.Payment.objects.create", side_effect=RuntimeError("test failure"))
    def test_failed_order_creation_rolls_back_its_hold(self, create_payment):
        with self.assertRaises(RuntimeError):
            self.create()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(StockReservation.objects.exists())
        self.assertEqual(available_quantity(self.product), 1)


class ConcurrentReservationsTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_customers_cannot_reserve_the_last_unit(self):
        machine, _ = Machine.objects.get_or_create(code="MACHINE-001", defaults={"name": "Main"})
        product = Product.objects.create(machine=machine, name="Coffee", price=100, machine_slot=1, stock_quantity=1)
        barrier = Barrier(2)

        def buy():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Client().post("/api/v1/orders/", {"product_id": product.pk, "phone": "0712345678"}, content_type="application/json").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: buy(), range(2)))
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(StockReservation.objects.filter(status="ACTIVE").count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 1)
