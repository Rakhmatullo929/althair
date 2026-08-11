from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
import urllib.error

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from ai_runtime.models import AIRun, AIRunOutcome
from ai_runtime.services import ensure_runtime_config, process_run, queue_for_inbound_message
from assistant_context.services import publish_assistant_profile
from crm.models import ContactIdentity, Conversation, ConversationAIState, Message
from gmail_integration.models import (
    GmailAutomationMode,
    GmailConnection,
    GmailConnectionStatus,
    GmailInitialSyncMode,
    GmailInitialSyncStatus,
    GmailMessageRecord,
    GmailNotification,
    GmailOAuthState,
    GmailOutboundAttempt,
    GmailSyncRun,
)
from gmail_integration.parser import parse_gmail_message, sanitize_html_to_text, strip_quoted_history
from gmail_integration.providers import (
    GMAIL_MODIFY_SCOPE,
    FakeGmailProvider,
    GmailHistoryExpired,
    GmailProviderError,
    GmailOAuthSnapshot,
    GmailSendResult,
    LiveGmailProvider,
)
from gmail_integration.services import (
    GmailError,
    bounded_full_sync,
    complete_oauth,
    connection_health,
    erase_gmail_contact_data,
    export_gmail_contact_data,
    gmail_autopilot_allowed,
    incremental_sync,
    process_notification,
    send_gmail_message,
    verify_pubsub_identity,
)
from gmail_integration.tasks import (
    check_gmail_connections,
    cleanup_gmail_operational_data,
    reconcile_gmail_history,
    renew_gmail_watches,
    retry_gmail_outbound,
)
from organizations.models import OrganizationMembership, OrganizationStatus
from organizations.services import create_organization


User = get_user_model()


@override_settings(
    DEBUG=True,
    GOOGLE_GMAIL_ENABLE_LIVE=False,
    GOOGLE_GMAIL_FAKE_PROVIDER=True,
    GOOGLE_GMAIL_CLIENT_ID="fake-client.apps.googleusercontent.com",
    GOOGLE_GMAIL_CLIENT_SECRET="test-only-client-secret",
    GOOGLE_GMAIL_REDIRECT_URI="http://testserver/api/v1/integrations/gmail/oauth/callback/",
    GOOGLE_GMAIL_PUBSUB_TOPIC="projects/test-project/topics/gmail-notifications",
    GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION="projects/test-project/subscriptions/gmail-push",
    GOOGLE_GMAIL_PUBSUB_AUDIENCE="http://testserver/api/v1/webhooks/gmail/pubsub/",
    GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT="gmail-push@test-project.iam.gserviceaccount.com",
    GOOGLE_GMAIL_FAKE_PUBSUB_TOKEN="test-only-google-pubsub-oidc",
    FIELD_ENCRYPTION_KEY="j64ChG14GGzpCY_wJAkkVx1fb0V3w_CVQvc--vvSeI8=",
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class GmailIntegrationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="gmail-owner", email="gmail-owner@example.test", password="pw12345!"
        )
        self.organization = create_organization(
            creator=self.owner, name="Gmail Clinic", slug="gmail-clinic"
        )
        self.membership = OrganizationMembership.objects.get(
            organization=self.organization, user=self.owner
        )
        self.other_user = User.objects.create_user(
            username="gmail-other", email="gmail-other@example.test", password="pw12345!"
        )
        self.other = create_organization(
            creator=self.other_user, name="Other Gmail", slug="other-gmail"
        )
        self.client.force_authenticate(self.owner)

    def tenant(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).id)}

    def start(self, redirect="/en/app/settings/channels/gmail"):
        return self.client.get(
            f"/api/v1/integrations/gmail/oauth/start/?redirect={redirect}", **self.tenant()
        )

    def connect(self, email="support@example.test", name="Support"):
        started = self.start()
        self.assertEqual(started.status_code, 200, started.data)
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.get(
                "/api/v1/integrations/gmail/oauth/callback/",
                {"state": started.data["state"], "code": f"fake_connect:{email}:{name}"},
            )
        self.assertEqual(response.status_code, 200, response.data)
        return GmailConnection.objects.get(pk=response.data["connection"]["id"])

    def inbound(self, connection, **overrides):
        body = {
            "sender": "Customer <customer@example.test>",
            "subject": "Need help",
            "text": "Hello, can you help?",
            **overrides,
        }
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.post(
                f"/api/v1/integrations/gmail/{connection.id}/test-inbound/",
                body,
                format="json",
                **self.tenant(),
            )
        self.assertEqual(response.status_code, 202, response.data)
        return response

    def test_oauth_uses_single_scope_pkce_hashed_state_and_write_only_tokens(self):
        started = self.start()
        self.assertEqual(started.data["scope"], "https://www.googleapis.com/auth/gmail.modify")
        state = GmailOAuthState.objects.get()
        self.assertNotEqual(state.state_hash, started.data["state"])
        self.assertEqual(state.state_hash, hashlib.sha256(started.data["state"].encode()).hexdigest())
        self.assertGreaterEqual(len(state.code_verifier), 43)
        connection = self.connect("oauth@example.test")
        detail = self.client.get(
            f"/api/v1/integrations/gmail/{connection.id}/", **self.tenant()
        )
        self.assertTrue(detail.data["has_encrypted_access_token"])
        self.assertTrue(detail.data["has_encrypted_refresh_token"])
        serialized = str(detail.data)
        self.assertNotIn("fake-access", serialized)
        self.assertNotIn("fake-refresh", serialized)
        self.assertNotIn("encrypted_credentials", serialized)

    def test_oauth_rejects_redirect_expiry_replay_wrong_user_and_duplicate_mailbox(self):
        self.assertEqual(self.start("https://evil.example/callback").status_code, 400)
        started = self.start()
        state = GmailOAuthState.objects.get(state_hash=hashlib.sha256(started.data["state"].encode()).hexdigest())
        state.expires_at = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["expires_at"])
        expired = self.client.get(
            "/api/v1/integrations/gmail/oauth/callback/",
            {"state": started.data["state"], "code": "fake_connect:expired@example.test:Expired"},
        )
        self.assertEqual(expired.status_code, 410)
        started = self.start()
        self.client.force_authenticate(self.other_user)
        wrong = self.client.get(
            "/api/v1/integrations/gmail/oauth/callback/",
            {"state": started.data["state"], "code": "fake_connect:wrong@example.test:Wrong"},
        )
        self.assertEqual(wrong.status_code, 403)
        self.client.force_authenticate(self.owner)
        complete_oauth(
            user=self.owner,
            raw_state=started.data["state"],
            code="fake_connect:unique@example.test:Unique",
        )
        with self.assertRaisesMessage(GmailError, "oauth_state_replayed"):
            complete_oauth(
                user=self.owner,
                raw_state=started.data["state"],
                code="fake_connect:unique@example.test:Unique",
            )
        self.connect("duplicate@example.test")
        another = self.start()
        duplicate = self.client.get(
            "/api/v1/integrations/gmail/oauth/callback/",
            {"state": another.data["state"], "code": "fake_connect:DUPLICATE@example.test:Other"},
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_tenant_scope_and_disconnect_removes_credentials_and_watch(self):
        connection = self.connect("tenant@example.test")
        inaccessible = self.client.get(
            f"/api/v1/integrations/gmail/{connection.id}/", **self.tenant(self.other)
        )
        self.assertIn(inaccessible.status_code, [403, 404])
        response = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/disconnect/",
            {}, format="json", **self.tenant(),
        )
        self.assertEqual(response.data["connection_status"], "disconnected")
        connection.refresh_from_db()
        self.assertEqual(connection.channel_connection.get_credentials(), {})
        self.assertIsNone(connection.watch_expiration_at)

    def test_pubsub_oidc_subscription_and_message_idempotency(self):
        connection = self.connect("push@example.test")
        data = base64.b64encode(json.dumps({"emailAddress": connection.mailbox_email, "historyId": "1001"}).encode()).decode()
        envelope = {
            "subscription": "projects/test-project/subscriptions/gmail-push",
            "message": {"messageId": "pubsub-1", "data": data},
        }
        raw = json.dumps(envelope).encode()
        denied = self.client.post(
            "/api/v1/webhooks/gmail/pubsub/", raw, content_type="application/json"
        )
        self.assertEqual(denied.status_code, 401)
        wrong_subscription = {**envelope, "subscription": "projects/evil/subscriptions/other"}
        forbidden = self.client.post(
            "/api/v1/webhooks/gmail/pubsub/",
            json.dumps(wrong_subscription).encode(),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-only-google-pubsub-oidc",
        )
        self.assertEqual(forbidden.status_code, 403)
        with self.captureOnCommitCallbacks(execute=False):
            first = self.client.post(
                "/api/v1/webhooks/gmail/pubsub/", raw, content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-only-google-pubsub-oidc",
            )
            second = self.client.post(
                "/api/v1/webhooks/gmail/pubsub/", raw, content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-only-google-pubsub-oidc",
            )
        self.assertEqual(first.data["accepted"], 1)
        self.assertEqual(second.data["duplicates"], 1)
        notification = GmailNotification.objects.get()
        process_notification(notification.id)
        notification.refresh_from_db()
        self.assertEqual(notification.status, "processed")

    def test_ingestion_creates_email_identity_thread_subject_and_is_idempotent(self):
        connection = self.connect("inbox@example.test")
        first = self.inbound(connection, message_id="gmail-inbound-1", thread_id="thread-1")
        second = self.inbound(connection, message_id="gmail-inbound-1", thread_id="thread-1")
        self.assertEqual(first.data["imported"], 1)
        self.assertEqual(second.data["imported"], 0)
        message = Message.objects.get(provider_message_id="gmail:gmail-inbound-1")
        self.assertEqual(message.organization, self.organization)
        self.assertEqual(message.conversation.subject, "Need help")
        identity = ContactIdentity.objects.get(contact=message.conversation.contact)
        self.assertEqual(identity.type, "email")
        self.assertEqual(identity.normalized_value, "customer@example.test")
        self.assertEqual(identity.channel_connection, connection.channel_connection)
        self.assertEqual(GmailMessageRecord.objects.count(), 1)

    def test_mime_sanitization_quote_stripping_attachments_and_loop_detection(self):
        encoded_html = base64.urlsafe_b64encode(
            b"<p>Hello <strong>team</strong></p><script>steal()</script><img src='x'>"
        ).decode().rstrip("=")
        payload = {
            "id": "mime-1", "threadId": "mime-thread", "historyId": "4", "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Robot <no-reply@example.test>"},
                    {"name": "To", "value": "support@example.test"},
                    {"name": "Subject", "value": "Automated"},
                    {"name": "Auto-Submitted", "value": "auto-generated"},
                    {"name": "X-Althair-Origin", "value": "message:123"},
                ],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": encoded_html}},
                    {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "att-1", "size": 25}},
                ],
            },
        }
        parsed = parse_gmail_message(payload, mailbox_email="support@example.test")
        self.assertEqual(parsed.body, "Hello team")
        self.assertTrue(parsed.is_automated)
        self.assertTrue(parsed.has_althair_origin)
        self.assertEqual(parsed.attachments[0]["filename"], "invoice.pdf")
        self.assertNotIn("script", sanitize_html_to_text("<script>x</script><p>safe</p>"))
        self.assertEqual(strip_quoted_history("Fresh reply\nOn Monday Person wrote:\nold"), "Fresh reply")

    def test_cursor_expiry_performs_bounded_full_sync(self):
        connection = self.connect("cursor@example.test")
        connection.history_id = "expired"
        connection.save(update_fields=["history_id"])
        run = incremental_sync(connection)
        self.assertEqual(run.sync_type, "full")
        self.assertEqual(run.fallback_reason, "history_cursor_expired")
        self.assertEqual(GmailSyncRun.objects.filter(connection=connection).count(), 2)

    def test_manual_send_uses_thread_headers_origin_marker_and_is_idempotent(self):
        connection = self.connect("send@example.test")
        self.inbound(connection, message_id="gmail-source", thread_id="send-thread")
        conversation = Conversation.objects.get(external_thread_id="send-thread")
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.send_reply.return_value = GmailSendResult(
                "gmail-sent-1", "send-thread", "request-1"
            )
            message, created = send_gmail_message(
                conversation=conversation,
                body="Thanks, we can help.",
                client_message_id="client-send-1",
                membership=self.membership,
            )
            duplicate, duplicate_created = send_gmail_message(
                conversation=conversation,
                body="Changed body must not send",
                client_message_id="client-send-1",
                membership=self.membership,
            )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(message, duplicate)
        self.assertEqual(provider.return_value.send_reply.call_count, 1)
        raw = provider.return_value.send_reply.call_args.kwargs["raw_message"]
        self.assertIn("In-Reply-To: <gmail-source@example.test>", raw)
        self.assertIn("X-Althair-Origin:", raw)
        self.assertEqual(message.status, "sent")
        conversation.refresh_from_db()
        self.assertEqual(conversation.ai_state, ConversationAIState.PAUSED_BY_HUMAN)
        self.assertTrue(GmailOutboundAttempt.objects.filter(message=message, status="sent").exists())

    def test_automation_policy_blocks_historical_and_automated_mail(self):
        connection = self.connect("ai@example.test")
        ensure_runtime_config(self.organization).enabled = True
        config = ensure_runtime_config(self.organization)
        config.enabled = True
        config.save(update_fields=["enabled"])
        connection.automation_mode = GmailAutomationMode.AUTOPILOT
        connection.save(update_fields=["automation_mode"])
        self.inbound(connection, message_id="ai-source", thread_id="ai-thread")
        conversation = Conversation.objects.get(external_thread_id="ai-thread")
        conversation.ai_state = ConversationAIState.AUTOPILOT_GMAIL
        conversation.save(update_fields=["ai_state"])
        self.assertTrue(gmail_autopilot_allowed(conversation))
        record = GmailMessageRecord.objects.get(message__conversation=conversation)
        record.is_automated = True
        record.save(update_fields=["is_automated"])
        self.assertFalse(gmail_autopilot_allowed(conversation))

    def test_health_and_watch_jobs_are_safe_and_deterministic(self):
        connection = self.connect("health@example.test")
        connection.watch_expiration_at = timezone.now() + timedelta(hours=1)
        connection.save(update_fields=["watch_expiration_at"])
        self.assertTrue(connection_health(connection, run_provider=True)["scope_valid"])
        self.assertEqual(renew_gmail_watches()["renewed"], 1)
        self.assertEqual(check_gmail_connections()["checked"], 1)

    def provider_response(self, payload):
        response = MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    @override_settings(
        GOOGLE_GMAIL_ENABLE_LIVE=True,
        GOOGLE_GMAIL_CLIENT_ID="live-client",
        GOOGLE_GMAIL_CLIENT_SECRET="live-secret",
        GOOGLE_GMAIL_REDIRECT_URI="https://api.example.test/api/v1/integrations/gmail/oauth/callback/",
        GOOGLE_GMAIL_PUBSUB_TOPIC="projects/live/topics/gmail",
    )
    def test_live_provider_oauth_watch_sync_send_attachment_and_health_contracts(self):
        provider = LiveGmailProvider()
        responses = [
            self.provider_response({
                "access_token": "live-access", "refresh_token": "live-refresh",
                "expires_in": 3600, "scope": "https://www.googleapis.com/auth/gmail.modify",
            }),
            self.provider_response({"emailAddress": "live@example.test", "historyId": "50"}),
        ]
        with patch("gmail_integration.providers.urllib.request.urlopen", side_effect=responses) as opened:
            snapshot = provider.exchange_code(code="oauth-code", code_verifier="v" * 64)
        self.assertEqual(snapshot.email, "live@example.test")
        self.assertEqual(snapshot.refresh_token, "live-refresh")
        token_request = opened.call_args_list[0].args[0]
        self.assertIn(b"code_verifier=", token_request.data)
        with override_settings(GOOGLE_GMAIL_ENABLE_LIVE=False):
            connection = self.connect("live@example.test")
        channel = connection.channel_connection
        channel.set_credentials({"access_token": "current-access", "refresh_token": "current-refresh"})
        channel.save(update_fields=["encrypted_credentials", "updated_at"])
        connection.token_expires_at = timezone.now() + timedelta(minutes=30)
        connection.save(update_fields=["token_expires_at"])
        expiration = int((timezone.now() + timedelta(days=6)).timestamp() * 1000)
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({"historyId": "51", "expiration": str(expiration)})):
            watch = provider.start_watch(connection)
        self.assertEqual(watch["history_id"], "51")
        self.assertGreater(watch["expiration"], timezone.now())
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({})):
            self.assertIsNone(provider.stop_watch(connection))
        payload = {"id": "live-message", "threadId": "live-thread", "payload": {"headers": []}}
        with patch(
            "gmail_integration.providers.urllib.request.urlopen",
            side_effect=[
                self.provider_response({"messages": [{"id": "live-message"}]}),
                self.provider_response(payload),
                self.provider_response({"historyId": "55"}),
            ],
        ):
            recent, cursor = provider.list_recent(connection, limit=10)
        self.assertEqual(recent[0]["id"], "live-message")
        self.assertEqual(cursor, "55")
        with patch(
            "gmail_integration.providers.urllib.request.urlopen",
            side_effect=[
                self.provider_response(
                    {
                        "history": [
                            {
                                "messagesAdded": [
                                    {"message": {"id": "m1"}},
                                    {"message": {"id": "m1"}},
                                ]
                            }
                        ],
                        "historyId": "59",
                        "nextPageToken": "next-page",
                    }
                ),
                self.provider_response(
                    {
                        "history": [
                            {"messagesAdded": [{"message": {"id": "m2"}}]}
                        ],
                        "historyId": "60",
                    }
                ),
            ],
        ) as opened:
            ids, cursor = provider.list_history(connection, start_history_id="55", limit=5)
        self.assertEqual(ids, ["m1", "m2"])
        self.assertEqual(cursor, "60")
        self.assertIn("pageToken=next-page", opened.call_args_list[1].args[0].full_url)
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({"id": "sent-live", "threadId": "live-thread"})):
            sent = provider.send_reply(connection, thread_id="live-thread", raw_message="Subject: Test\n\nHello")
        self.assertEqual(sent.message_id, "sent-live")
        attachment_data = base64.urlsafe_b64encode(b"safe attachment").decode().rstrip("=")
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({"data": attachment_data})):
            self.assertEqual(provider.get_attachment(connection, message_id="m1", attachment_id="a1"), b"safe attachment")
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({"emailAddress": "live@example.test"})):
            self.assertTrue(provider.health(connection)["mailbox_matches"])

    def test_live_provider_refresh_and_bounded_error_mapping(self):
        provider = LiveGmailProvider()
        with override_settings(GOOGLE_GMAIL_ENABLE_LIVE=False):
            connection = self.connect("refresh@example.test")
        connection.token_expires_at = timezone.now() - timedelta(minutes=1)
        connection.save(update_fields=["token_expires_at"])
        with patch("gmail_integration.providers.urllib.request.urlopen", return_value=self.provider_response({"access_token": "renewed", "expires_in": 1200})):
            self.assertEqual(provider._token(connection), "renewed")
        connection.refresh_from_db()
        self.assertGreater(connection.token_expires_at, timezone.now())
        history_error = urllib.error.HTTPError(
            "https://gmail.googleapis.com/gmail/v1/users/me/history", 404, "not found", {}, None
        )
        with patch("gmail_integration.providers.urllib.request.urlopen", side_effect=history_error):
            with self.assertRaises(GmailHistoryExpired):
                provider._request("https://gmail.googleapis.com/gmail/v1/users/me/history")
        rate_error = urllib.error.HTTPError("https://gmail.googleapis.com", 429, "rate", {}, None)
        with patch("gmail_integration.providers.urllib.request.urlopen", side_effect=rate_error):
            with self.assertRaisesMessage(GmailProviderError, "provider_rate_limited"):
                provider._request("https://gmail.googleapis.com/gmail/v1/users/me/profile")

    def test_attachment_download_and_operational_cleanup(self):
        connection = self.connect("attachment@example.test")
        encoded = base64.urlsafe_b64encode(b"%PDF-1.4 invoice bytes").decode().rstrip("=")
        payload = {
            "id": "attachment-message", "threadId": "attachment-thread", "historyId": "2000", "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Customer <files@example.test>"},
                    {"name": "To", "value": connection.mailbox_email},
                    {"name": "Subject", "value": "Invoice"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Attached").decode()}},
                    {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "attachment-1", "data": encoded, "size": 13}},
                ],
            },
        }
        channel = connection.channel_connection
        channel.configuration = {**channel.configuration, "fake_messages": {"attachment-message": payload}, "fake_pending_message_ids": ["attachment-message"], "fake_history_id": "2000"}
        channel.save(update_fields=["configuration", "updated_at"])
        incremental_sync(connection, target_history_id="2000")
        record = GmailMessageRecord.objects.get(gmail_message_id="attachment-message")
        response = self.client.get(
            f"/api/v1/integrations/gmail/attachments/{record.id}/0/", **self.tenant()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.4 invoice bytes")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            self.client.get(f"/api/v1/integrations/gmail/attachments/{record.id}/9/", **self.tenant()).status_code,
            404,
        )
        self.assertEqual(cleanup_gmail_operational_data()["notifications"], 0)

    def test_retry_and_reconciliation_task_paths(self):
        connection = self.connect("retry@example.test")
        self.inbound(connection, message_id="retry-source", thread_id="retry-thread")
        conversation = Conversation.objects.get(external_thread_id="retry-thread")
        with patch("gmail_integration.tasks.retry_gmail_outbound.apply_async"), patch(
            "gmail_integration.services.gmail_provider"
        ) as provider:
            provider.return_value.send_reply.side_effect = GmailProviderError("temporary", transient=True)
            message, _ = send_gmail_message(
                conversation=conversation, body="[gmail-transient-error]", client_message_id="retry-client", membership=self.membership
            )
        attempt = GmailOutboundAttempt.objects.get(message=message)
        with patch("gmail_integration.tasks.gmail_provider") as provider:
            provider.return_value.send_reply.return_value = GmailSendResult("retry-sent", "retry-thread", "retry-request")
            self.assertEqual(retry_gmail_outbound(str(attempt.id))["status"], "sent")
        self.assertEqual(reconcile_gmail_history()["failed"], 0)
        connection.failure_count = 3
        connection.last_health_check_at = timezone.now()
        connection.save(update_fields=["failure_count", "last_health_check_at"])
        self.assertEqual(reconcile_gmail_history()["circuit_open"], 1)

    def test_tenant_operations_and_autopilot_validation(self):
        connection = self.connect("operations@example.test")
        listed = self.client.get("/api/v1/integrations/gmail/", **self.tenant())
        self.assertEqual(listed.data["count"], 1)
        health = self.client.get(
            f"/api/v1/integrations/gmail/{connection.id}/health/", **self.tenant()
        )
        self.assertTrue(health.data["watch_active"])
        refreshed = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/health/", {}, format="json", **self.tenant()
        )
        self.assertTrue(refreshed.data["provider_reachable"])
        renewed = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/watch/renew/", {}, format="json", **self.tenant()
        )
        self.assertEqual(renewed.status_code, 200)
        resync = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/resync/", {}, format="json", **self.tenant()
        )
        self.assertEqual(resync.status_code, 202)
        suggest = self.client.patch(
            f"/api/v1/integrations/gmail/{connection.id}/",
            {"automation_mode": "suggest"}, format="json", **self.tenant(),
        )
        self.assertEqual(suggest.data["automation_mode"], "suggest")
        autopilot = self.client.patch(
            f"/api/v1/integrations/gmail/{connection.id}/",
            {"automation_mode": "autopilot"}, format="json", **self.tenant(),
        )
        self.assertEqual(autopilot.status_code, 400)

    def test_reconnect_preserves_existing_refresh_token_and_rejects_mailbox_swap(self):
        connection = self.connect("reconnect@example.test")
        previous_refresh = connection.channel_connection.get_credentials()["refresh_token"]
        started = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/reconnect/",
            {"redirect": f"/en/app/settings/channels/gmail/{connection.id}"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(started.status_code, 200, started.data)
        snapshot = GmailOAuthSnapshot(
            access_token="replacement-access",
            refresh_token="",
            expires_in=3600,
            scope=(GMAIL_MODIFY_SCOPE,),
            email=connection.mailbox_email,
            name="Reconnected",
            google_user_id="google-reconnected",
        )
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.exchange_code.return_value = snapshot
            provider.return_value.start_watch.return_value = {
                "history_id": "3000",
                "expiration": timezone.now() + timedelta(days=7),
            }
            updated = complete_oauth(
                user=self.owner,
                raw_state=started.data["state"],
                code="safe-reconnect-code",
            )
        credentials = updated.channel_connection.get_credentials()
        self.assertEqual(credentials["access_token"], "replacement-access")
        self.assertEqual(credentials["refresh_token"], previous_refresh)

        mismatch = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/reconnect/",
            {"redirect": f"/en/app/settings/channels/gmail/{connection.id}"},
            format="json",
            **self.tenant(),
        )
        wrong_snapshot = GmailOAuthSnapshot(
            access_token="wrong-access",
            refresh_token="wrong-refresh",
            expires_in=3600,
            scope=(GMAIL_MODIFY_SCOPE,),
            email="different@example.test",
            name="Wrong",
            google_user_id="google-wrong",
        )
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.exchange_code.return_value = wrong_snapshot
            response = self.client.get(
                "/api/v1/integrations/gmail/oauth/callback/",
                {"state": mismatch.data["state"], "code": "safe-mismatch-code"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "reconnect_mailbox_mismatch")

    def test_oauth_initial_options_missing_scope_and_label_validation(self):
        started = self.client.get(
            "/api/v1/integrations/gmail/oauth/start/",
            {
                "redirect": "/en/app/settings/channels/gmail",
                "initial_sync_mode": "from_now",
                "initial_sync_max_messages": 17,
            },
            **self.tenant(),
        )
        self.assertEqual(started.status_code, 200)
        state = GmailOAuthState.objects.get(
            state_hash=hashlib.sha256(started.data["state"].encode()).hexdigest()
        )
        self.assertEqual(state.initial_sync_mode, GmailInitialSyncMode.FROM_NOW)
        self.assertEqual(state.initial_sync_max_messages, 17)
        missing_scope = GmailOAuthSnapshot(
            access_token="access",
            refresh_token="refresh",
            expires_in=3600,
            scope=(),
            email="scope@example.test",
            name="Scope",
            google_user_id="scope-user",
        )
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.exchange_code.return_value = missing_scope
            response = self.client.get(
                "/api/v1/integrations/gmail/oauth/callback/",
                {"state": started.data["state"], "code": "safe-scope-code"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "required_gmail_scope_missing")

        connection = self.connect("labels@example.test")
        invalid = self.client.patch(
            f"/api/v1/integrations/gmail/{connection.id}/",
            {"excluded_label_ids": ["TRASH"]},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(invalid.status_code, 400)

    def test_initial_sync_is_bounded_from_now_and_cancellable(self):
        connection = self.connect("initial@example.test")
        connection.initial_sync_mode = GmailInitialSyncMode.RECENT
        connection.initial_sync_max_messages = 2
        connection.initial_sync_status = GmailInitialSyncStatus.RUNNING
        connection.save(
            update_fields=[
                "initial_sync_mode",
                "initial_sync_max_messages",
                "initial_sync_status",
            ]
        )
        payloads = [
            {
                "id": f"initial-{index}",
                "threadId": f"initial-thread-{index}",
                "historyId": str(4000 + index),
                "labelIds": ["INBOX"],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": f"customer{index}@example.test"},
                        {"name": "To", "value": connection.mailbox_email},
                        {"name": "Subject", "value": "Historical"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(b"Historical body").decode()
                    },
                },
            }
            for index in range(5)
        ]
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.list_recent.return_value = (payloads, "5000")
            run = bounded_full_sync(connection, sync_type="initial")
        self.assertEqual(run.imported_count, 2)
        self.assertEqual(
            GmailMessageRecord.objects.filter(connection=connection, is_historical=True).count(),
            2,
        )
        self.assertFalse(
            AIRun.objects.filter(conversation__channel_connection=connection.channel_connection).exists()
        )

        connection.initial_sync_mode = GmailInitialSyncMode.FROM_NOW
        connection.initial_sync_status = GmailInitialSyncStatus.RUNNING
        connection.save(update_fields=["initial_sync_mode", "initial_sync_status"])
        with patch("gmail_integration.services.gmail_provider") as provider:
            from_now = bounded_full_sync(connection, sync_type="initial")
        self.assertEqual(from_now.imported_count, 0)
        provider.return_value.list_recent.assert_not_called()

        connection.initial_sync_status = GmailInitialSyncStatus.RUNNING
        connection.save(update_fields=["initial_sync_status"])
        cancelled = self.client.post(
            f"/api/v1/integrations/gmail/{connection.id}/sync/cancel/",
            {},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.data["initial_sync_status"], "cancelled")

    def test_encoded_charset_reply_to_cc_encryption_and_signature_parsing(self):
        body = "Привет команда\n-- \nСтарая подпись".encode("koi8-r")
        payload = {
            "id": "encoded-1",
            "threadId": "encoded-thread",
            "historyId": "6100",
            "internalDate": "1704067200000",
            "snippet": "encoded preview",
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "multipart/encrypted",
                "headers": [
                    {"name": "From", "value": "=?UTF-8?B?0JrQu9C40LXQvdGC?= <sender@example.test>"},
                    {"name": "To", "value": "Support <encoded@example.test>"},
                    {"name": "Cc", "value": "Finance <finance@example.test>"},
                    {"name": "Reply-To", "value": "reply@example.test"},
                    {"name": "Subject", "value": "=?UTF-8?B?0J/RgNC40LLQtdGC?="},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Content-Type", "value": "text/plain; charset=koi8-r"}
                        ],
                        "body": {"data": base64.urlsafe_b64encode(body).decode()},
                    },
                    {"mimeType": "application/pgp-encrypted", "body": {}},
                ],
            },
        }
        parsed = parse_gmail_message(payload, mailbox_email="encoded@example.test")
        self.assertEqual(parsed.sender_name, "Клиент")
        self.assertEqual(parsed.subject, "Привет")
        self.assertEqual(parsed.body, "Привет команда")
        self.assertEqual(parsed.reply_to, "reply@example.test")
        self.assertEqual(parsed.cc_recipients, ("finance@example.test",))
        self.assertEqual(parsed.snippet, "encoded preview")
        self.assertTrue(parsed.is_encrypted)
        self.assertIsNotNone(parsed.occurred_at)

    def test_reply_uses_reply_to_and_validated_cc(self):
        connection = self.connect("cc@example.test")
        self.inbound(
            connection,
            message_id="cc-source",
            thread_id="cc-thread",
        )
        record = GmailMessageRecord.objects.get(gmail_message_id="cc-source")
        record.reply_to = "reply-to@example.test"
        record.save(update_fields=["reply_to"])
        conversation = record.message.conversation
        with patch("gmail_integration.services.gmail_provider") as provider:
            provider.return_value.send_reply.return_value = GmailSendResult(
                "cc-sent", "cc-thread", "cc-request"
            )
            message, _ = send_gmail_message(
                conversation=conversation,
                body="Cc reply",
                client_message_id="cc-client",
                membership=self.membership,
                cc=["finance@example.test", connection.mailbox_email, "finance@example.test"],
            )
        raw = provider.return_value.send_reply.call_args.kwargs["raw_message"]
        self.assertIn("To: reply-to@example.test", raw)
        self.assertIn("Cc: finance@example.test", raw)
        self.assertEqual(message.metadata["cc"], ["finance@example.test"])
        with self.assertRaisesMessage(GmailError, "cc_invalid"):
            send_gmail_message(
                conversation=conversation,
                body="Bad cc",
                client_message_id="cc-invalid",
                membership=self.membership,
                cc=["not-an-email"],
            )

    def test_privacy_export_anonymize_retention_and_cross_tenant_scope(self):
        connection = self.connect("privacy@example.test")
        self.inbound(connection, message_id="privacy-source", thread_id="privacy-thread")
        record = GmailMessageRecord.objects.get(gmail_message_id="privacy-source")
        contact = record.message.conversation.contact
        exported = export_gmail_contact_data(connection=connection, contact_id=contact.id)
        self.assertEqual(exported["organization_id"], str(self.organization.id))
        self.assertEqual(exported["messages"][0]["body"], "Hello, can you help?")

        cross_tenant = self.client.get(
            f"/api/v1/integrations/gmail/{connection.id}/privacy/",
            {"contact_id": str(contact.id)},
            **self.tenant(self.other),
        )
        self.assertIn(cross_tenant.status_code, [403, 404])
        result = erase_gmail_contact_data(
            connection=connection,
            contact_id=contact.id,
            mode="anonymize",
            actor=self.membership,
        )
        self.assertEqual(result["messages_affected"], 1)
        record.message.refresh_from_db()
        self.assertEqual(record.message.body, "[redacted by privacy request]")
        self.assertFalse(
            ContactIdentity.objects.filter(
                contact=contact, channel_connection=connection.channel_connection
            ).exists()
        )

        second = self.connect("retention@example.test")
        self.inbound(second, message_id="retention-source", thread_id="retention-thread")
        old = GmailMessageRecord.objects.get(gmail_message_id="retention-source")
        Message.objects.filter(pk=old.message_id).update(
            occurred_at=timezone.now() - timedelta(days=3)
        )
        second.retention_days = 1
        second.save(update_fields=["retention_days"])
        cleanup = cleanup_gmail_operational_data()
        self.assertEqual(cleanup["messages_redacted"], 1)
        old.message.refresh_from_db()
        self.assertEqual(old.message.body, "[redacted by retention policy]")

    def test_suspended_organization_pubsub_and_send_fail_closed(self):
        connection = self.connect("suspended@example.test")
        self.inbound(connection, message_id="suspended-source", thread_id="suspended-thread")
        self.organization.status = OrganizationStatus.SUSPENDED
        self.organization.save(update_fields=["status"])
        data = base64.urlsafe_b64encode(
            json.dumps(
                {"emailAddress": connection.mailbox_email, "historyId": "8000"}
            ).encode()
        ).decode().rstrip("=")
        response = self.client.post(
            "/api/v1/webhooks/google/gmail-pubsub/",
            json.dumps(
                {
                    "subscription": "projects/test-project/subscriptions/gmail-push",
                    "message": {"messageId": "suspended-push", "data": data},
                }
            ).encode(),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-only-google-pubsub-oidc",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["ignored"], 1)
        conversation = Conversation.objects.get(external_thread_id="suspended-thread")
        with self.assertRaisesMessage(GmailError, "organization_read_only"):
            send_gmail_message(
                conversation=conversation,
                body="Must not send",
                client_message_id="suspended-send",
                membership=self.membership,
            )

    @override_settings(GOOGLE_GMAIL_FAKE_PROVIDER=False, GOOGLE_GMAIL_ENABLE_LIVE=True)
    def test_pubsub_oidc_claims_fail_closed(self):
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value={
                "email": "attacker@example.test",
                "email_verified": True,
                "iss": "https://accounts.google.com",
            },
        ):
            with self.assertRaisesMessage(GmailError, "pubsub_identity_invalid"):
                verify_pubsub_identity("Bearer signed-but-wrong-identity")

    def test_out_of_order_cursor_and_attachment_content_validation(self):
        connection = self.connect("cursor-order@example.test")
        connection.history_id = "9000"
        connection.save(update_fields=["history_id"])
        run = incremental_sync(connection, target_history_id="8500")
        connection.refresh_from_db()
        self.assertEqual(connection.history_id, "9000")
        self.assertEqual(run.end_history_id, "9000")

        self.inbound(
            connection,
            message_id="bad-attachment",
            thread_id="bad-attachment-thread",
            attachment=True,
        )
        record = GmailMessageRecord.objects.get(gmail_message_id="bad-attachment")
        configuration = connection.channel_connection.configuration
        configuration["fake_messages"]["bad-attachment"]["payload"]["parts"][1][
            "body"
        ]["data"] = base64.urlsafe_b64encode(b"not a pdf").decode()
        connection.channel_connection.configuration = configuration
        connection.channel_connection.save(update_fields=["configuration", "updated_at"])
        response = self.client.get(
            f"/api/v1/integrations/gmail/attachments/{record.id}/0/",
            **self.tenant(),
        )
        self.assertEqual(response.status_code, 415)

    def test_superuser_without_membership_and_unknown_mailbox_have_no_tenant_bypass(self):
        connection = self.connect("no-bypass@example.test")
        superuser = User.objects.create_superuser(
            username="gmail-superuser",
            email="gmail-superuser@example.test",
            password="pw12345!",
        )
        self.client.force_authenticate(superuser)
        denied = self.client.get(
            "/api/v1/integrations/gmail/",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertIn(denied.status_code, [403, 404])

        self.client.force_authenticate(None)
        data = base64.urlsafe_b64encode(
            json.dumps(
                {"emailAddress": "unknown@example.test", "historyId": "9100"}
            ).encode()
        ).decode().rstrip("=")
        ignored = self.client.post(
            "/api/v1/webhooks/google/gmail-pubsub/",
            json.dumps(
                {
                    "subscription": "projects/test-project/subscriptions/gmail-push",
                    "message": {"messageId": "unknown-mailbox", "data": data},
                }
            ).encode(),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-only-google-pubsub-oidc",
        )
        self.assertEqual(ignored.status_code, 202)
        self.assertEqual(ignored.data, {"accepted": 0, "ignored": 1})
        self.assertFalse(
            GmailNotification.objects.filter(connection=connection).exists()
        )

    def test_gmail_autopilot_uses_published_context_and_backend_send(self):
        connection = self.connect("autopilot@example.test")
        profile = self.organization.assistant_profile
        profile.assistant_name = "Mail Assistant"
        profile.business_summary = "Customer support"
        profile.business_description = "Support by email"
        profile.products_services = "Email help"
        profile.introduction = "I am the support assistant."
        profile.fallback_response = "A team member will help."
        profile.updated_by = self.owner
        profile.full_clean()
        profile.save()
        publish_assistant_profile(profile=profile, actor=self.owner)
        config = ensure_runtime_config(self.organization)
        config.enabled = True
        config.save(update_fields=["enabled"])
        config.allowed_channel_connections.add(connection.channel_connection)
        connection.automation_mode = GmailAutomationMode.AUTOPILOT
        connection.save(update_fields=["automation_mode"])
        self.inbound(
            connection,
            message_id="autopilot-source",
            thread_id="autopilot-thread",
            text="Please tell me about email help.",
        )
        inbound = Message.objects.get(provider_message_id="gmail:autopilot-source")
        inbound.conversation.refresh_from_db()
        self.assertEqual(inbound.conversation.ai_state, ConversationAIState.AUTOPILOT_GMAIL)
        run, created = queue_for_inbound_message(inbound.id)
        self.assertTrue(created)
        process_run(run.id)
        run.refresh_from_db()
        self.assertEqual(run.outcome, AIRunOutcome.SENT_GMAIL_REPLY)
        self.assertTrue(
            Message.objects.filter(
                conversation=inbound.conversation, direction="outbound", sender_type="ai", status="sent"
            ).exists()
        )
        self.assertEqual(AIRun.objects.get(pk=run.pk).ai_context_revision.version, 1)
