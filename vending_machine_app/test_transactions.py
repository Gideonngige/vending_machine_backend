from datetime import datetime, timezone as utc

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from .models import Machine, Product, Order, Payment, PaymentAttempt, DispenseRequest


class TransactionTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="owner", is_staff=True)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(self.staff)}"
        self.machine = Machine.objects.get(code="MACHINE-001")
        self.other = Machine.objects.create(code="CAMPUS", name="Campus")
        self.product = Product.objects.create(machine=self.machine, name="Coffee", price=100, machine_slot=1)
        self.other_product = Product.objects.create(machine=self.other, name="Latte", price=150, machine_slot=1)
        self.base = "/api/v1/management/transactions/"

    def order(self, reference, status="PENDING", product=None):
        order = Order.objects.create(reference=reference, product=product or self.product, customer_phone="254712345678")
        payment = Payment.objects.create(order=order, phone_number=order.customer_phone,
                                        amount=order.total_amount, status=status,
                                        mpesa_receipt_number="RECEIPT" if status == "SUCCESS" else "")
        return order, payment

    def attempt(self, payment, status):
        return PaymentAttempt.objects.create(payment=payment, phone_number=payment.phone_number,
                                             amount=payment.amount, status=status,
                                             mpesa_receipt_number=f"R-{payment.attempts.count()}")

    def test_staff_jwt_required_for_list_and_detail(self):
        order, _ = self.order("PROTECTED")
        for staff in (False, None):
            self.client.defaults.clear()
            if staff is False:
                user = get_user_model().objects.create_user(username="customer")
                self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(user)}"
            for path in (self.base, self.base + order.reference + "/"):
                self.assertEqual(self.client.get(path).status_code, 401 if staff is None else 403)

    def test_filters_combine_without_leaking_other_machine_orders(self):
        self.order("PAID-MAIN", "SUCCESS")
        self.order("PAID-CAMPUS", "SUCCESS", self.other_product)
        self.order("FAILED-MAIN", "FAILED")
        data = self.client.get(self.base, {"machine": self.machine.code, "reference": "paid", "payment_status": "SUCCESS", "order_status": "PENDING"}).json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["reference"], "PAID-MAIN")
        self.assertEqual(self.client.get(self.base, {"machine": "MISSING"}).status_code, 404)

    def test_attention_flags_match_filters_without_duplicate_rows(self):
        order, payment = self.order("REVIEW", "SUCCESS")
        self.attempt(payment, "SUCCESS")
        self.attempt(payment, "SUCCESS")
        self.attempt(payment, "UNKNOWN")
        DispenseRequest.objects.create(order=order, machine_slot=1, status="FAILED", error_message="Controller error")
        expected = {"paid_not_dispensed", "extra_payments", "uncertain", "dispense_failed", "unverified"}
        row = self.client.get(self.base).json()["results"][0]
        self.assertEqual(set(row["attention"]), expected)
        for flag in expected:
            data = self.client.get(self.base, {"attention": flag}).json()
            self.assertEqual(data["count"], 1)
            self.assertEqual(len(data["results"]), 1)

    def test_completed_dispense_is_not_flagged_but_missing_completion_is(self):
        order, _ = self.order("COMPLETE", "SUCCESS")
        DispenseRequest.objects.create(order=order, machine_slot=1, status="READY", completed_at=timezone.now())
        other, _ = self.order("MISSING-TIME", "SUCCESS")
        DispenseRequest.objects.create(order=other, machine_slot=1, status="READY")
        no_dispense, _ = self.order("NO-DISPENSE", "SUCCESS")
        data = self.client.get(self.base, {"attention": "paid_not_dispensed"}).json()
        self.assertEqual({row["reference"] for row in data["results"]}, {other.reference, no_dispense.reference})

    def test_detail_includes_receipts_attempts_and_dispense_errors(self):
        order, payment = self.order("DETAIL", "SUCCESS")
        attempt = self.attempt(payment, "SUCCESS")
        DispenseRequest.objects.create(order=order, machine_slot=1, status="FAILED", error_message="Out of water")
        data = self.client.get(self.base + order.reference + "/").json()
        self.assertEqual(data["payment"]["receipt"], "RECEIPT")
        self.assertEqual(data["payment"]["id"], payment.pk)
        self.assertEqual(data["attempts"][0]["id"], attempt.pk)
        self.assertEqual(data["dispense"]["error_message"], "Out of water")
        self.assertEqual(self.client.get(self.base + "MISSING/").status_code, 404)
        self.assertEqual(self.client.post(self.base + order.reference + "/", {}).status_code, 405)

    def test_missing_related_records_have_safe_empty_values(self):
        order = Order.objects.create(product=self.product, customer_phone="254712345678")
        data = self.client.get(self.base + order.reference + "/").json()
        self.assertIsNone(data["payment"])
        self.assertIsNone(data["dispense"])
        self.assertEqual(data["attempts"], [])

    def test_inclusive_dates_use_reporting_timezone(self):
        previous, _ = self.order("PREVIOUS")
        today, _ = self.order("LOCAL-TODAY")
        Order.objects.filter(pk=previous.pk).update(created_at=datetime(2026, 10, 19, 20, tzinfo=utc.utc))
        Order.objects.filter(pk=today.pk).update(created_at=datetime(2026, 10, 19, 22, tzinfo=utc.utc))
        with timezone.override("Africa/Nairobi"):
            data = self.client.get(self.base, {"start_date": "2026-10-20", "end_date": "2026-10-20"}).json()
        self.assertEqual([row["reference"] for row in data["results"]], [today.reference])
        self.assertEqual(data["timezone"], "Africa/Nairobi")

    def test_pagination_and_invalid_filters(self):
        self.order("FIRST")
        self.order("SECOND")
        first = self.client.get(self.base, {"page_size": 1}).json()
        second = self.client.get(self.base, {"page_size": 1, "page": 2}).json()
        self.assertEqual(first["count"], 2)
        self.assertIsNotNone(first["next"])
        self.assertNotEqual(first["results"][0]["reference"], second["results"][0]["reference"])
        for filters in ({"start_date": "bad"}, {"payment_status": "bad"}, {"page": 0}, {"page_size": 101},
                        {"attention": "bad"}, {"start_date": "2026-10-21", "end_date": "2026-10-20"},
                        {"end_date": "9999-12-31"}):
            with self.subTest(filters=filters):
                self.assertEqual(self.client.get(self.base, filters).status_code, 400)
