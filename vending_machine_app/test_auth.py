from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings


class StaffAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner", password="test-password", is_staff=True)
        cls.customer = get_user_model().objects.create_user(username="customer", password="test-password")

    def setUp(self):
        self.browser = Client(enforce_csrf_checks=True)

    def sign_in(self, username="owner"):
        token = self.browser.get("/api/v1/auth/session/").json()["csrf_token"]
        return self.browser.post("/api/v1/auth/login/", {
            "username": username, "password": "test-password",
        }, content_type="application/json", HTTP_X_CSRFTOKEN=token)

    def test_staff_can_login_restore_and_access_reports(self):
        self.assertEqual(self.browser.get("/api/v1/auth/session/").json()["user"], None)
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(self.browser.get("/api/v1/auth/session/").json()["user"]["username"], "owner")
        self.assertEqual(self.browser.get("/api/v1/reports/sales/").status_code, 200)
        self.assertEqual(self.browser.get("/admin/").status_code, 200)

    def test_customer_and_inactive_staff_cannot_login(self):
        self.assertEqual(self.sign_in("customer").status_code, 401)
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.sign_in().status_code, 401)

    def test_login_and_logout_require_csrf(self):
        self.assertEqual(self.browser.post("/api/v1/auth/login/", {}).status_code, 403)
        self.sign_in()
        self.assertEqual(self.browser.post("/api/v1/auth/logout/").status_code, 403)

    def test_logout_invalidates_session(self):
        token = self.sign_in().json()["csrf_token"]
        self.assertEqual(self.browser.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(self.browser.get("/api/v1/auth/session/").json()["user"], None)
        self.assertIn(self.browser.get("/api/v1/reports/sales/").status_code, (401, 403))

    def test_superuser_uses_the_same_login(self):
        self.owner.is_superuser = True
        self.owner.save()
        data = self.sign_in().json()
        self.assertTrue(data["user"]["is_superuser"])
        self.assertTrue(self.browser.cookies["sessionid"]["httponly"])

    def test_untrusted_origin_cannot_login_even_with_token(self):
        token = self.browser.get("/api/v1/auth/session/").json()["csrf_token"]
        response = self.browser.post("/api/v1/auth/login/", {
            "username": "owner", "password": "test-password",
        }, content_type="application/json", HTTP_X_CSRFTOKEN=token,
            HTTP_ORIGIN="https://untrusted.example")
        self.assertEqual(response.status_code, 403)

    @override_settings(FRONTEND_WEB_URL="https://coffee.example")
    def test_admin_login_redirects_to_react_without_external_next_redirect(self):
        response = self.browser.get("/admin/login/?next=https://untrusted.example")
        self.assertRedirects(response, "https://coffee.example/#/owner/login", fetch_redirect_response=False)
