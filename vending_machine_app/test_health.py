from unittest.mock import patch

from django.db import OperationalError
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase


class HealthTests(TestCase):
    def test_health_does_not_query_database(self):
        with self.assertNumQueries(0):
            response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_ready_with_migrated_database(self):
        response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_ready_returns_503_for_database_failure_without_details(self):
        with patch("vending_machine_app.health.connection.cursor", side_effect=OperationalError("private connection details")):
            with self.assertLogs("vending_machine_app.health", level="WARNING"):
                response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})
        self.assertNotContains(response, "private", status_code=503)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_ready_returns_503_for_pending_migrations(self):
        with patch("vending_machine_app.health.MigrationExecutor") as executor:
            executor.return_value.migration_plan.return_value = [object()]
            response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_probes_are_public_get_only_and_ignore_invalid_bearer_headers(self):
        for endpoint in ("health", "ready"):
            url = f"/api/v1/{endpoint}/"
            self.assertEqual(self.client.get(url, HTTP_AUTHORIZATION="Bearer invalid").status_code, 200)
            self.assertEqual(self.client.post(url).status_code, 405)

    def test_missing_migration_record_is_detected_in_real_database(self):
        MigrationRecorder.Migration.objects.filter(app="vending_machine_app", name="0004_stock_reservations").delete()
        self.assertEqual(self.client.get("/api/v1/ready/").status_code, 503)

    def test_inconsistent_migration_history_is_not_ready(self):
        with patch("vending_machine_app.health.MigrationExecutor") as executor:
            executor.return_value.loader.check_consistent_history.side_effect = InconsistentMigrationHistory("private migration details")
            with self.assertLogs("vending_machine_app.health", level="WARNING"):
                response = self.client.get("/api/v1/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})
