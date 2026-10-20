from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework_simplejwt.tokens import AccessToken

from .models import Machine, Product, Order


class ManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="owner", is_staff=True)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(self.staff)}"
        self.machine = Machine.objects.get(code="MACHINE-001")
        self.other = Machine.objects.create(code="OTHER", name="Campus")
        self.product = Product.objects.create(machine=self.machine, name="Coffee", price=100, machine_slot=1)
        self.base = "/api/v1/management/"

    def post(self, resource, data):
        return self.client.post(self.base + resource + "/", data, content_type="application/json")

    def patch(self, resource, pk, data):
        if resource == "products" and "expected_updated_at" not in data:
            data = {**data, "expected_updated_at": Product.objects.get(pk=pk).updated_at.isoformat()}
        return self.client.patch(f"{self.base}{resource}/{pk}/", data, content_type="application/json")

    def product_data(self, **changes):
        return {"machine": self.machine.pk, "name": "Latte", "price": "150.00",
                "machine_slot": 2, "stock_quantity": 25, "volume_ml": 250, **changes}

    def test_all_endpoints_require_staff_bearer_authentication(self):
        for kind in ("anonymous", "customer"):
            if kind == "anonymous":
                self.client.defaults.clear()
            else:
                user = get_user_model().objects.create_user(username="customer")
                self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(user)}"
            expected = 401 if kind == "anonymous" else 403
            for resource, pk in (("machines", self.machine.pk), ("products", self.product.pk)):
                self.assertEqual(self.client.get(f"{self.base}{resource}/").status_code, expected)
                self.assertEqual(self.post(resource, {}).status_code, expected)
                self.assertEqual(self.patch(resource, pk, {}).status_code, expected)

    def test_create_and_edit_machine(self):
        response = self.post("machines", {"code": "NEW-003", "name": "Office", "location": "Nairobi"})
        self.assertEqual(response.status_code, 201)
        pk = response.json()["id"]
        response = self.patch("machines", pk, {"name": "Updated", "is_enabled": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Machine.objects.get(pk=pk).is_enabled)
        self.assertEqual(self.patch("machines", pk, {"code": "CHANGED"}).status_code, 400)
        self.assertEqual(self.post("machines", {"code": "NEW-003", "name": "Duplicate"}).status_code, 400)

    def test_create_product_and_scope_menu(self):
        response = self.post("products", self.product_data())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["machine_code"], self.machine.code)
        self.assertEqual(self.client.get("/api/v1/products/").json()["products"][1]["name"], "Latte")

    def test_slots_are_unique_per_machine(self):
        self.assertEqual(self.post("products", self.product_data(machine_slot=1)).status_code, 400)
        self.assertEqual(self.post("products", self.product_data(machine=self.other.pk, machine_slot=1)).status_code, 201)

    def test_invalid_price_stock_slot_volume_and_url_are_rejected(self):
        for fields in ({"price": "0"}, {"price": "100.50"}, {"price": "NaN"},
                       {"stock_quantity": -1}, {"stock_quantity": 1.5},
                       {"machine_slot": 0}, {"volume_ml": 0}, {"image_url": "not-a-url"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.post("products", self.product_data(**fields)).status_code, 400)

    def test_product_update_preserves_order_snapshots_and_machine(self):
        order = Order.objects.create(product=self.product, customer_phone="254712345678")
        response = self.patch("products", self.product.pk, {
            "price": "200.00", "name": "Renamed", "stock_quantity": 7, "is_available": False,
        })
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(str(order.unit_price), "100.00")
        self.assertEqual(order.product_name, "Coffee")
        self.assertEqual(self.patch("products", self.product.pk, {"machine": self.other.pk}).status_code, 400)
        self.assertEqual(self.patch("products", self.product.pk, {"machine_slot": 3}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/products/").json()["products"], [])

    def test_catalog_includes_disabled_machines_and_hidden_products(self):
        self.other.is_enabled = False
        self.other.save()
        hidden = Product.objects.create(machine=self.other, name="Hidden", price=100, machine_slot=1, is_available=False)
        machines = self.client.get(self.base + "machines/").json()["machines"]
        self.assertEqual(len(machines), 2)
        products = self.client.get(self.base + "products/?machine=OTHER").json()["products"]
        self.assertEqual([p["id"] for p in products], [hidden.pk])

    def test_history_cannot_be_deleted_through_management_endpoints(self):
        self.assertEqual(self.client.delete(f"{self.base}products/{self.product.pk}/").status_code, 405)
        self.assertEqual(self.client.delete(f"{self.base}machines/{self.machine.pk}/").status_code, 405)

    def test_stale_stock_edit_cannot_overwrite_newer_inventory(self):
        loaded_at = self.product.updated_at.isoformat()
        self.product.stock_quantity = 9
        self.product.save()
        response = self.patch("products", self.product.pk, {
            "expected_updated_at": loaded_at, "stock_quantity": 100,
        })
        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 9)
