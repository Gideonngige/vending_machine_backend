from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


class StaffAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner", password="test-password", is_staff=True)
        cls.customer = get_user_model().objects.create_user(username="customer", password="test-password")

    def setUp(self):
        self.browser = Client(enforce_csrf_checks=True)

    def post(self, path, data):
        return self.browser.post(f"/api/v1/auth/{path}/", data, content_type="application/json")

    def sign_in(self, username="owner"):
        return self.post("login", {"username": username, "password": "test-password"})

    def test_login_returns_tokens_without_setting_cookies(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)
        data = response.json()
        self.assertEqual(data["user"]["username"], "owner")
        self.assertEqual(AccessToken(data["access"])["token_type"], "access")
        self.assertEqual(RefreshToken(data["refresh"])["token_type"], "refresh")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(self.browser.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f'Bearer {data["access"]}').status_code, 200)
        self.assertEqual(self.browser.get("/api/v1/reports/sales/", HTTP_AUTHORIZATION=f'Bearer {data["access"]}').status_code, 200)

    def test_customer_and_inactive_staff_cannot_login(self):
        self.assertEqual(self.sign_in("customer").status_code, 401)
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.sign_in().status_code, 401)

    def test_invalid_credentials_and_malformed_body(self):
        self.assertEqual(self.post("login", {"username": "owner", "password": "wrong"}).status_code, 401)
        self.assertEqual(self.post("login", []).status_code, 400)
        self.assertEqual(self.post("login", {"username": "owner"}).status_code, 400)

    def test_refresh_rotates_and_old_token_cannot_be_replayed(self):
        original = self.sign_in().json()
        response = self.post("refresh", {"refresh": original["refresh"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)
        self.assertNotEqual(response.json()["refresh"], original["refresh"])
        self.assertEqual(self.post("refresh", {"refresh": original["refresh"]}).status_code, 401)
        self.assertEqual(self.post("refresh", {"refresh": response.json()["refresh"]}).status_code, 200)

    def test_logout_revokes_refresh_and_does_not_set_cookies(self):
        data = self.sign_in().json()
        response = self.post("logout", {"refresh": data["refresh"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.cookies)
        self.assertEqual(self.post("refresh", {"refresh": data["refresh"]}).status_code, 401)
        self.assertEqual(self.post("logout", {"refresh": data["refresh"]}).status_code, 200)
        # Access JWTs expire independently after five minutes.
        self.assertEqual(self.browser.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f'Bearer {data["access"]}').status_code, 200)

    def test_expired_or_tampered_access_is_rejected(self):
        expired = AccessToken.for_user(self.owner)
        expired.set_exp(lifetime=timedelta(seconds=-1))
        for value in (str(expired), "invalid-token"):
            self.assertEqual(self.browser.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {value}").status_code, 401)

    def test_refresh_rejects_access_token_and_missing_token(self):
        data = self.sign_in().json()
        self.assertEqual(self.post("refresh", {"refresh": data["access"]}).status_code, 401)
        self.assertEqual(self.post("refresh", {}).status_code, 400)
        self.assertEqual(self.post("logout", {}).status_code, 400)

    def test_demoted_staff_cannot_refresh_or_read_reports(self):
        data = self.sign_in().json()
        self.owner.is_staff = False
        self.owner.save()
        self.assertEqual(self.post("refresh", {"refresh": data["refresh"]}).status_code, 403)
        self.assertEqual(self.browser.get("/api/v1/reports/sales/", HTTP_AUTHORIZATION=f'Bearer {data["access"]}').status_code, 403)

    def test_password_change_invalidates_access_and_refresh(self):
        data = self.sign_in().json()
        self.owner.set_password("changed-password")
        self.owner.save()
        self.assertEqual(self.post("refresh", {"refresh": data["refresh"]}).status_code, 401)
        self.assertEqual(self.browser.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f'Bearer {data["access"]}').status_code, 401)

    def test_session_cookie_alone_cannot_access_api(self):
        self.browser.force_login(self.owner)
        self.assertEqual(self.browser.get("/api/v1/reports/sales/").status_code, 401)
        self.assertEqual(self.browser.get("/api/v1/auth/me/").status_code, 401)

    def test_superuser_uses_same_jwt_login_without_admin_session(self):
        self.owner.is_superuser = True
        self.owner.save()
        response = self.sign_in()
        self.assertTrue(response.json()["user"]["is_superuser"])
        self.assertFalse(response.cookies)
        self.assertEqual(self.browser.get("/admin/").status_code, 302)
