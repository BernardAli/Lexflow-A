"""Password recovery through the public forms and real reset emails."""
from datetime import datetime, timedelta
import re
from unittest import mock
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.testing import PASSWORD, make_office


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class PasswordResetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.office, cls.users = make_office("recovery")
        cls.user = cls.users["clerk"]
        cls.user.email = "clerk@example.test"
        cls.user.save(update_fields=["email"])

    def request_reset(self, email=None, **kwargs):
        response = self.client.post(reverse("core:password_reset"), {"email": email or self.user.email}, **kwargs)
        self.assertRedirects(response, reverse("core:password_reset_done"))
        return response

    def email_link(self):
        return re.search(r"https?://[^\s]+", mail.outbox[-1].body).group()

    def confirm_link(self, user=None, token=None):
        user = user or self.user
        return reverse("core:password_reset_confirm", kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": token or default_token_generator.make_token(user),
        })

    def test_login_links_to_recovery_and_keeps_the_password_masked(self):
        response = self.client.get(reverse("core:login"))
        self.assertContains(response, 'href="' + reverse("core:password_reset") + '"')
        self.assertContains(response, 'type="password"')
        self.assertContains(response, 'aria-label="Show password"')
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, "js/password-toggle.js")

    def test_request_sends_a_secure_account_bound_link(self):
        self.request_reset("CLERK@example.test", secure=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertEqual(mail.outbox[0].subject, "Reset your LexFlow password")
        self.assertTrue(self.email_link().startswith("https://testserver/password-reset/confirm/"))
        self.assertIn(self.user.username, mail.outbox[0].body)
        self.assertNotIn(PASSWORD, mail.outbox[0].body)

    def test_unknown_inactive_and_unusable_accounts_get_the_same_confirmation(self):
        User = get_user_model()
        User.objects.create_user("inactive", email="inactive@example.test", password=PASSWORD, is_active=False)
        User.objects.create_user("unusable", email="unusable@example.test", password=None)
        for email in ("unknown@example.test", "inactive@example.test", "unusable@example.test"):
            with self.subTest(email=email):
                response = self.request_reset(email)
                self.assertContains(self.client.get(response.url), "If an eligible account uses that email address")
                self.assertEqual(len(mail.outbox), 0)

    def test_invalid_email_stays_on_form_without_sending(self):
        response = self.client.post(reverse("core:password_reset"), {"email": "invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertEqual(len(mail.outbox), 0)

    def test_email_link_resets_password_invalidates_old_sessions_and_cannot_be_reused(self):
        previous_session = Client()
        self.assertTrue(previous_session.login(username=self.user.username, password=PASSWORD))
        self.request_reset()
        original_link = urlsplit(self.email_link()).path
        response = self.client.get(original_link, follow=True)
        self.assertTrue(response.context["validlink"])
        self.assertNotIn(original_link.rsplit("/", 2)[1], response.request["PATH_INFO"])
        new_password = "A-new-office-password-2031!"
        response = self.client.post(response.request["PATH_INFO"], {
            "new_password1": new_password, "new_password2": new_password,
        })
        self.assertRedirects(response, reverse("core:password_reset_complete"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.assertFalse(self.user.check_password(PASSWORD))
        self.assertRedirects(previous_session.get(reverse("core:dashboard")),
                             reverse("core:login") + "?next=/", fetch_redirect_response=False)
        replay = Client().get(original_link, follow=True)
        self.assertFalse(replay.context["validlink"])
        self.assertContains(replay, "This reset link is unavailable")
        response = self.client.post(reverse("core:login"), {
            "username": self.user.username, "password": new_password,
        })
        self.assertRedirects(response, reverse("core:dashboard"))

    def test_new_password_validation_preserves_the_existing_password(self):
        response = self.client.get(self.confirm_link(), follow=True)
        path = response.request["PATH_INFO"]
        for first, second in (("short", "short"), ("A-new-office-password-2031!", "Different-password-2031!")):
            with self.subTest(first=first):
                response = self.client.post(path, {"new_password1": first, "new_password2": second})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertContains(response, 'aria-invalid="true"')
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(PASSWORD))

    def test_expired_link_is_rejected(self):
        issued_at = datetime(2026, 10, 23, 12)
        with mock.patch.object(default_token_generator, "_now", return_value=issued_at):
            link = self.confirm_link()
        with mock.patch.object(default_token_generator, "_now", return_value=issued_at + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1)):
            response = self.client.get(link)
        self.assertFalse(response.context["validlink"])
        self.assertContains(response, "Request a new link")

    def test_invalid_and_other_account_tokens_are_rejected(self):
        wrong_account = self.confirm_link(self.users["notary"], default_token_generator.make_token(self.user))
        for link in (self.confirm_link(token="invalid-token"), wrong_account,
                     reverse("core:password_reset_confirm", kwargs={"uidb64": "invalid", "token": "invalid"})):
            with self.subTest(link=link):
                response = self.client.get(link)
                self.assertFalse(response.context["validlink"])
                self.assertNotContains(response, 'name="new_password1"')

    def test_both_reset_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("core:password_reset"), {"email": self.user.email})
        self.assertEqual(response.status_code, 403)
        response = client.get(self.confirm_link(), follow=True)
        response = client.post(response.request["PATH_INFO"], {
            "new_password1": "A-new-office-password-2031!", "new_password2": "A-new-office-password-2031!",
        })
        self.assertEqual(response.status_code, 403)
