from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from organizations.models import Organization, OrganizationInvitation, OrganizationMembership
from organizations.services import create_invitation, create_organization

User = get_user_model()


class ClientAuthenticationTests(APITestCase):
    def csrf_client(self):
        client = APIClient(enforce_csrf_checks=True)
        response = client.get(reverse("users:auth-csrf"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        token = response.json()["csrftoken"]
        return client, {"HTTP_X_CSRFTOKEN": token}

    def test_registration_creates_user_organization_profile_and_owner_atomically(self):
        client, csrf_header = self.csrf_client()
        response = client.post(
            reverse("users:auth-register"),
            {
                "first_name": "Aziza",
                "last_name": "Karimova",
                "email": "aziza@example.test",
                "password": "test-only-correct-horse-battery-92!",
                "organization_name": "Aziza Studio",
                "industry": "beauty",
                "default_language": "uz",
                "timezone": "Asia/Tashkent",
            },
            format="json",
            **csrf_header,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        user = User.objects.get(email="aziza@example.test")
        organization = Organization.objects.get(pk=response.json()["organization_id"])
        self.assertEqual(organization.profile.public_business_name, "Aziza Studio")
        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=user,
                organization=organization,
                role="owner",
                status="active",
            ).exists()
        )
        self.assertIn("jwt-auth", response.cookies)
        self.assertTrue(response.cookies["jwt-auth"]["httponly"])

    def test_registration_conflict_is_generic(self):
        User.objects.create_user(
            username="exists@example.test",
            email="exists@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        client, csrf_header = self.csrf_client()
        response = client.post(
            reverse("users:auth-register"),
            {
                "first_name": "Existing",
                "email": "exists@example.test",
                "password": "test-only-correct-horse-battery-92!",
                "organization_name": "Existing Company",
            },
            format="json",
            **csrf_header,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("already exists", str(response.json()).lower())

    def test_login_current_user_refresh_and_logout_cookie_flow_requires_csrf(self):
        user = User.objects.create_user(
            username="login@example.test",
            email="login@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        create_organization(creator=user, name="Login Co", slug="login-co")
        client, csrf_header = self.csrf_client()
        missing = client.post(
            reverse("users:auth-login"),
            {
                "email": user.email,
                "password": "test-only-correct-horse-battery-92!",
            },
            format="json",
        )
        self.assertEqual(missing.status_code, status.HTTP_403_FORBIDDEN)
        login = client.post(
            reverse("users:auth-login"),
            {
                "email": user.email,
                "password": "test-only-correct-horse-battery-92!",
            },
            format="json",
            **csrf_header,
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        me = client.get(reverse("me"))
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.json()["email"], user.email)
        refreshed = client.post(reverse("users:auth-refresh"), {}, format="json", **csrf_header)
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)
        logout = client.post(reverse("users:auth-logout"), {}, format="json", **csrf_header)
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.assertEqual(client.get(reverse("me")).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_error_does_not_distinguish_unknown_email(self):
        user = User.objects.create_user(
            username="known@example.test",
            email="known@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        client, csrf_header = self.csrf_client()
        wrong = client.post(
            reverse("users:auth-login"),
            {"email": user.email, "password": "wrong-password"},
            format="json",
            **csrf_header,
        )
        unknown = client.post(
            reverse("users:auth-login"),
            {"email": "unknown@example.test", "password": "wrong-password"},
            format="json",
            **csrf_header,
        )
        self.assertEqual((wrong.status_code, wrong.json()), (unknown.status_code, unknown.json()))

    @override_settings(DEBUG=True, CLIENT_APP_URL="http://localhost:3001")
    def test_invitation_create_inspect_accept_expire_and_reuse(self):
        owner = User.objects.create_user(
            username="owner@example.test",
            email="owner@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        organization = create_organization(creator=owner, name="Invite Co", slug="invite-co")
        self.client.force_authenticate(owner)
        invitation_response = self.client.post(
            reverse("organizations:invitation-list", args=[organization.id]),
            {"email": "member@example.test", "role": "agent"},
            format="json",
            HTTP_X_ORGANIZATION_ID=str(organization.id),
        )
        self.assertEqual(invitation_response.status_code, status.HTTP_201_CREATED)
        invitation_url = invitation_response.json()["invitation_url"]
        raw_token = invitation_url.rsplit("/", 1)[-1]
        self.client.force_authenticate(user=None)
        inspect = self.client.post(
            reverse("users:invitation-inspect"), {"token": raw_token}, format="json",
        )
        self.assertEqual(inspect.json()["state"], "pending")
        accepted = self.client.post(
            reverse("users:invitation-accept"),
            {
                "token": raw_token,
                "first_name": "Member",
                "password": "test-only-correct-horse-battery-93!",
            },
            format="json",
        )
        self.assertEqual(accepted.status_code, status.HTTP_201_CREATED, accepted.json())
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=organization,
                user__email="member@example.test",
                role="agent",
                status="active",
            ).exists()
        )
        reused = self.client.post(
            reverse("users:invitation-accept"), {"token": raw_token}, format="json",
        )
        self.assertEqual(reused.status_code, status.HTTP_409_CONFLICT)

        expired, expired_token = create_invitation(
            organization=organization,
            email="expired@example.test",
            role="viewer",
            invited_by=owner,
            expires_in=timedelta(seconds=-1),
        )
        expired_response = self.client.post(
            reverse("users:invitation-accept"),
            {
                "token": expired_token,
                "password": "test-only-correct-horse-battery-94!",
            },
            format="json",
        )
        self.assertEqual(expired_response.status_code, status.HTTP_410_GONE)
        expired.refresh_from_db()
        self.assertEqual(expired.status, "expired")

    @override_settings(DEBUG=True, CLIENT_APP_URL="http://localhost:3001")
    def test_password_reset_is_generic_and_single_use(self):
        user = User.objects.create_user(
            username="reset@example.test",
            email="reset@example.test",
            password="test-only-correct-horse-battery-92!",
        )
        client, csrf_header = self.csrf_client()
        known = client.post(
            reverse("users:password-reset-request"),
            {"email": user.email},
            format="json",
            **csrf_header,
        )
        unknown = client.post(
            reverse("users:password-reset-request"),
            {"email": "missing@example.test"},
            format="json",
            **csrf_header,
        )
        self.assertEqual(known.json()["detail"], unknown.json()["detail"])
        token = known.json()["development_reset_url"].rsplit("/", 1)[-1]
        confirmed = client.post(
            reverse("users:password-reset-confirm"),
            {"token": token, "password": "New-correct-horse-battery-95!"},
            format="json",
            **csrf_header,
        )
        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        reused = client.post(
            reverse("users:password-reset-confirm"),
            {"token": token, "password": "New-correct-horse-battery-96!"},
            format="json",
            **csrf_header,
        )
        self.assertEqual(reused.status_code, status.HTTP_410_GONE)
