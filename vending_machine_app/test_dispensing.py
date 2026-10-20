from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections
from io import StringIO
from uuid import uuid4
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken
from .dispensing import record_event, TransitionError
from .models import Machine, Product, Order, Payment, DispenseRequest, DispenseEvent, RecoveryNote


class DispensingTests(TestCase):
    def setUp(self):
        self.machine = Machine.objects.get_or_create(code="MACHINE-001", defaults={"name": "Test machine"})[0]
        self.product = Product.objects.create(machine=self.machine, name="Coffee", price=100, machine_slot=1, stock_quantity=4)
        self.order = Order.objects.create(product=self.product, customer_phone="254712345678", status="PAID")
        self.payment = Payment.objects.create(order=self.order, amount=100, phone_number=self.order.customer_phone, status="SUCCESS", verified_at=timezone.now())
        self.dispense = DispenseRequest.objects.create(order=self.order, machine_slot=1)
        self.url = f"/api/v1/management/transactions/{self.order.reference}/recovery/"
        self.staff = get_user_model().objects.create_user("owner", is_staff=True)

    def event(self, state, **kwargs):
        return record_event(self.order.reference, state, kwargs.pop("event_id", uuid4()), source="simulator", **kwargs)

    def test_lifecycle_and_retry_do_not_change_stock_or_payment(self):
        event_id = uuid4()
        self.event("DISPENSING", event_id=event_id)
        self.event("READY")
        self.event("DISPENSING", event_id=event_id)  # late delivery of identical event
        self.dispense.refresh_from_db()
        completed = self.dispense.completed_at
        self.event("COLLECTED")
        self.dispense.refresh_from_db(); self.product.refresh_from_db(); self.payment.refresh_from_db()
        self.assertEqual(self.dispense.status, "COLLECTED")
        self.assertEqual(self.dispense.completed_at, completed)
        self.assertIsNotNone(self.dispense.started_at)
        self.assertIsNotNone(self.dispense.collected_at)
        self.assertEqual(DispenseEvent.objects.count(), 3)
        self.assertEqual(self.product.stock_quantity, 4)
        self.assertEqual(self.payment.status, "SUCCESS")

    def test_skip_regression_and_reused_event_are_rejected(self):
        with self.assertRaises(TransitionError): self.event("READY")
        event_id = uuid4()
        self.event("DISPENSING", event_id=event_id)
        with self.assertRaises(TransitionError): self.event("READY", event_id=event_id)
        with self.assertRaises(TransitionError): self.event("COLLECTED")
        self.assertEqual(DispenseEvent.objects.count(), 1)

    def test_unverified_paid_order_cannot_start(self):
        self.payment.verified_at = None; self.payment.save()
        with self.assertRaises(TransitionError): self.event("DISPENSING")
        self.assertFalse(DispenseEvent.objects.exists())

    def test_inconsistent_or_cancelled_order_cannot_start(self):
        for state in ["PENDING", "CANCELLED", "FAILED"]:
            self.order.status = state; self.order.save()
            with self.assertRaises(TransitionError): self.event("DISPENSING")

    def test_failure_requires_reason_and_cannot_restart(self):
        self.event("DISPENSING")
        with self.assertRaises(TransitionError): self.event("FAILED")
        self.event("FAILED", message="Water unavailable")
        with self.assertRaises(TransitionError): self.event("DISPENSING")
        self.dispense.refresh_from_db(); self.payment.refresh_from_db()
        self.assertEqual(self.dispense.error_message, "Water unavailable")
        self.assertIsNone(self.dispense.completed_at)
        self.assertEqual(self.payment.status, "SUCCESS")

    def test_disabled_machine_blocks_start_but_accepts_inflight_failure(self):
        self.machine.is_enabled = False; self.machine.save()
        with self.assertRaises(TransitionError): self.event("DISPENSING")
        self.machine.is_enabled = True; self.machine.save()
        self.event("DISPENSING")
        self.machine.is_enabled = False; self.machine.save()
        self.event("FAILED", message="Stopped")

    @override_settings(DEBUG=False)
    def test_simulator_is_disabled_in_production(self):
        with self.assertRaises(CommandError):
            call_command("simulate_dispense", self.order.reference, "DISPENSING", stdout=StringIO())

    @override_settings(DEBUG=True)
    def test_simulator_records_visible_source(self):
        call_command("simulate_dispense", self.order.reference, "DISPENSING", stdout=StringIO())
        self.assertEqual(DispenseEvent.objects.get().source, "simulator")

    def test_recovery_requires_staff_and_records_actor_without_money_changes(self):
        body = {"action": "REFUND_RECORDED", "note": "External reference ABC; operator confirmed."}
        self.assertEqual(self.client.post(self.url, body).status_code, 401)
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(self.staff)}"
        self.assertEqual(self.client.post(self.url, body).status_code, 201)
        self.assertEqual(RecoveryNote.objects.get().actor, self.staff)
        self.payment.refresh_from_db(); self.order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.payment.status, "SUCCESS")
        self.assertEqual(self.order.status, "PAID")
        self.assertEqual(self.product.stock_quantity, 4)
        detail = self.client.get(f"/api/v1/management/transactions/{self.order.reference}/").json()
        self.assertEqual(detail["recovery_notes"][0]["action"], "REFUND_RECORDED")

    def test_recovery_validation_and_unpaid_order(self):
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {AccessToken.for_user(self.staff)}"
        for body in [{"action": "RESOLVED", "note": " "}, {"action": "DISPENSE", "note": "test"}]:
            self.assertEqual(self.client.post(self.url, body).status_code, 400)
        self.payment.status = "PENDING"; self.payment.save()
        self.assertEqual(self.client.post(self.url, {"action": "INVESTIGATING", "note": "test"}).status_code, 400)
        self.assertFalse(RecoveryNote.objects.exists())


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentDispensingTests(TransactionTestCase):
    setUp = DispensingTests.setUp

    def run_events(self, states, event_ids):
        gate = Barrier(2)
        reference = self.order.reference
        def worker(pair):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                record_event(reference, pair[0], pair[1], source="simulator", message="test" if pair[0] == "FAILED" else "")
                return "ok"
            except TransitionError:
                return "conflict"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, zip(states, event_ids)))

    def test_concurrent_duplicate_is_applied_once(self):
        event_id = uuid4()
        self.assertEqual(self.run_events(["DISPENSING", "DISPENSING"], [event_id, event_id]), ["ok", "ok"])
        self.assertEqual(DispenseEvent.objects.count(), 1)

    def test_competing_completion_and_failure_have_one_winner(self):
        record_event(self.order.reference, "DISPENSING", uuid4(), source="simulator")
        self.assertCountEqual(self.run_events(["READY", "FAILED"], [uuid4(), uuid4()]), ["ok", "conflict"])
        self.order.refresh_from_db(); self.dispense.refresh_from_db()
        self.assertEqual(self.order.status, self.dispense.status)
        self.assertEqual(DispenseEvent.objects.count(), 2)
