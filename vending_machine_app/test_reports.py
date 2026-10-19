from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Machine, Product, Order, Payment, DispenseRequest


class SalesReportTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="owner", is_staff=True)
        self.machine = Machine.objects.get(code="MACHINE-001")
        self.product = Product.objects.create(machine=self.machine, name="Coffee", price=100, machine_slot=1)
        self.url = "/api/v1/reports/sales/"

    def paid_order(self, quantity=1, paid_at=None, product=None):
        product = product or self.product
        order = Order.objects.create(product=product, quantity=quantity, customer_phone="254712345678")
        Payment.objects.create(order=order, amount=order.total_amount,
                               phone_number=order.customer_phone, status="SUCCESS",
                               paid_at=paid_at or timezone.now())
        return order

    def test_access_requires_staff(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
        user = get_user_model().objects.create_user(username="customer")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_payment_and_dispensing_are_counted_separately(self):
        order = self.paid_order(quantity=2)
        self.paid_order()
        unpaid = Order.objects.create(product=self.product, customer_phone="254712345678")
        Payment.objects.create(order=unpaid, amount=100, phone_number=unpaid.customer_phone, status="FAILED")
        DispenseRequest.objects.create(order=order, machine_slot=1, status="READY", completed_at=timezone.now())
        self.client.force_login(self.staff)
        data = self.client.get(self.url).json()
        self.assertEqual(data["today"], {"revenue": "300", "paid_orders": 2, "coffees_dispensed": 2})
        self.assertEqual(data["by_product"][0]["paid_orders"], 2)

    def test_machine_filter_and_missing_machine(self):
        self.paid_order()
        other = Machine.objects.create(code="SECOND", name="Campus")
        product = Product.objects.create(machine=other, name="Tea", price=50, machine_slot=1)
        self.paid_order(product=product)
        self.client.force_login(self.staff)
        data = self.client.get(self.url, {"machine": "SECOND"}).json()
        self.assertEqual(data["all_time"]["revenue"], "50")
        self.assertEqual(len(data["by_machine"]), 1)
        self.assertEqual(self.client.get(self.url, {"machine": "MISSING"}).status_code, 404)

    def test_reporting_day_uses_local_timezone(self):
        # 22:00 UTC is already the next day in Nairobi.
        self.paid_order(paid_at=datetime(2026, 10, 19, 22, tzinfo=dt_timezone.utc))
        self.paid_order(paid_at=datetime(2026, 10, 19, 20, tzinfo=dt_timezone.utc))
        self.client.force_login(self.staff)
        with timezone.override("Africa/Nairobi"), patch(
            "vending_machine_app.api_views.reports.timezone.localdate",
            return_value=datetime(2026, 10, 20).date(),
        ):
            data = self.client.get(self.url).json()
        self.assertEqual(data["today"]["paid_orders"], 1)
        self.assertEqual(data["week"]["paid_orders"], 2)
        self.assertEqual(data["timezone"], "Africa/Nairobi")
