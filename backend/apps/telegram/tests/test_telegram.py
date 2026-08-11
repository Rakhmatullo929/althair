from __future__ import annotations

import json
import urllib.error
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection as database_connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from ai_runtime.models import AIHandoff, AIRun
from ai_runtime.services import ensure_runtime_config, set_conversation_ai_state
from channels.models import ChannelStatus
from crm.models import ContactIdentity, Conversation, ConversationAIState, Message, MessageStatus
from organizations.models import OrganizationMembership
from organizations.services import create_organization
from telegram.models import (
    TelegramAuditEvent,
    TelegramAutomationMode,
    TelegramBotConnection,
    TelegramEventStatus,
    TelegramManagedBotRequest,
    TelegramOutboundAttempt,
    TelegramUserLink,
    TelegramWebhookEvent,
)
from telegram.providers import (
    BaseTelegramProvider,
    LiveTelegramProvider,
    TelegramProviderError,
)
from telegram.services import (
    connection_health,
    expire_pending_requests,
    process_webhook_event,
    send_telegram_message,
)
from telegram.tasks import (
    check_telegram_connections,
    expire_telegram_requests,
    process_telegram_manager_event,
    process_telegram_webhook,
    retry_telegram_outbound,
)


User = get_user_model()


@override_settings(
    DEBUG=True,
    TELEGRAM_ENABLE_LIVE=False,
    TELEGRAM_FAKE_PROVIDER=True,
    TELEGRAM_MANAGER_BOT_USERNAME="AlthairManagerBot",
    TELEGRAM_MANAGER_WEBHOOK_SECRET="test-only-manager-webhook-secret",
    TELEGRAM_BOT_WEBHOOK_BASE_URL="http://testserver/api/v1/webhooks/telegram/bots",
    FIELD_ENCRYPTION_KEY="j64ChG14GGzpCY_wJAkkVx1fb0V3w_CVQvc--vvSeI8=",
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class TelegramManagedBotsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="telegram-owner",
            email="telegram-owner@example.test",
            password="pw12345!",
        )
        self.organization = create_organization(
            creator=self.owner,
            name="Telegram Clinic",
            slug="telegram-clinic",
        )
        self.membership = OrganizationMembership.objects.get(
            organization=self.organization,
            user=self.owner,
        )
        self.other_user = User.objects.create_user(
            username="telegram-other",
            email="telegram-other@example.test",
            password="pw12345!",
        )
        self.other = create_organization(
            creator=self.other_user,
            name="Other Telegram",
            slug="other-telegram",
        )
        self.client.force_authenticate(self.owner)

    def tenant(self, organization=None):
        return {
            "HTTP_X_ORGANIZATION_ID": str(
                (organization or self.organization).id
            )
        }

    def link_identity(self, telegram_user_id=700001):
        response = self.client.post(
            "/api/v1/integrations/telegram/identity/",
            {},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        start = parse_qs(urlparse(response.data["telegram_url"]).query)["start"][0]
        confirmed = self.client.post(
            "/api/v1/integrations/telegram/test-manager-event/",
            {
                "event_type": "identity_link",
                "start_parameter": start,
                "telegram_user_id": telegram_user_id,
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(confirmed.status_code, 202, confirmed.data)
        link = TelegramUserLink.objects.get(user=self.owner, status="linked")
        self.assertNotIn(start.removeprefix("link_"), link.token_hash)
        return link

    def connect_managed(self, username="ClinicSupportBot"):
        link = self.link_identity()
        created = self.client.post(
            "/api/v1/integrations/telegram/managed-requests/",
            {"suggested_name": "Clinic Support", "suggested_username": username},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data["creation_url"].startswith("https://t.me/newbot/"))
        confirmed = self.client.post(
            "/api/v1/integrations/telegram/test-manager-event/",
            {"event_type": "managed_bot", "request_id": created.data["request"]["id"]},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(confirmed.status_code, 202, confirmed.data)
        connection = TelegramBotConnection.objects.get(organization=self.organization)
        self.assertEqual(connection.owner_telegram_user_id, link.telegram_user_id)
        return connection

    def connect_existing(self, bot_id=810001, username="ExistingSupportBot"):
        response = self.client.post(
            "/api/v1/integrations/telegram/existing-bot/",
            {"token": f"test-only-existing:{bot_id}:{username}:Existing Support"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(response.status_code, 201, response.data)
        return TelegramBotConnection.objects.get(pk=response.data["id"])

    def test_readiness_is_explicit_and_fake_is_test_only(self):
        response = self.client.get(
            "/api/v1/integrations/telegram/readiness/", **self.tenant()
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ready"])
        self.assertTrue(response.data["fake_provider"])
        with override_settings(DEBUG=False, TESTING=False, TELEGRAM_FAKE_PROVIDER=True):
            response = self.client.get(
                "/api/v1/integrations/telegram/readiness/", **self.tenant()
            )
            self.assertFalse(response.data["enabled"])
            self.assertFalse(response.data["fake_provider"])

    def test_identity_link_is_hashed_expiring_single_use_and_takeover_safe(self):
        link = self.link_identity()
        self.assertGreater(link.expires_at, timezone.now())
        self.client.force_authenticate(self.other_user)
        second = self.client.post(
            "/api/v1/integrations/telegram/identity/",
            {},
            format="json",
            **self.tenant(self.other),
        )
        start = parse_qs(urlparse(second.data["telegram_url"]).query)["start"][0]
        denied = self.client.post(
            "/api/v1/integrations/telegram/test-manager-event/",
            {
                "event_type": "identity_link",
                "start_parameter": start,
                "telegram_user_id": link.telegram_user_id,
            },
            format="json",
            **self.tenant(self.other),
        )
        self.assertEqual(denied.status_code, 202)
        self.assertEqual(TelegramUserLink.objects.get(id=second.data["id"]).status, "pending")

    def test_managed_creation_requires_link_and_exact_owner_confirmation(self):
        missing = self.client.post(
            "/api/v1/integrations/telegram/managed-requests/",
            {"suggested_name": "Clinic", "suggested_username": "ClinicBot"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(missing.status_code, 409)
        self.link_identity()
        created = self.client.post(
            "/api/v1/integrations/telegram/managed-requests/",
            {"suggested_name": "Clinic", "suggested_username": "A_bot"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(created.status_code, 201, created.data)
        request = TelegramManagedBotRequest.objects.get(pk=created.data["request"]["id"])
        self.assertEqual(request.suggested_username, "A_bot")
        self.assertNotIn("token", str(created.data).lower())

    def test_managed_bot_token_is_encrypted_write_only_and_audited(self):
        connection = self.connect_managed()
        credentials = connection.channel_connection.get_credentials()
        self.assertTrue(credentials["bot_token"].startswith("test-only-"))
        with database_connection.cursor() as cursor:
            cursor.execute(
                "SELECT encrypted_credentials FROM channels_channelconnection WHERE id = %s",
                [str(connection.channel_connection_id).replace("-", "")],
            )
            stored_credentials = cursor.fetchone()[0]
        self.assertNotIn(credentials["bot_token"], stored_credentials)
        payload = self.client.get(
            f"/api/v1/integrations/telegram/{connection.id}/", **self.tenant()
        ).data
        self.assertTrue(payload["has_encrypted_token"])
        self.assertNotIn("bot_token", str(payload))
        self.assertNotIn("webhook_secret", str(payload))
        self.assertTrue(
            TelegramAuditEvent.objects.filter(
                organization=self.organization,
                event_type="telegram.connection.created",
            ).exists()
        )

    def test_one_dedicated_bot_per_company_and_cross_tenant_detail_denied(self):
        connection = self.connect_existing()
        duplicate = self.client.post(
            "/api/v1/integrations/telegram/existing-bot/",
            {"token": "test-only-existing:810002:SecondSupportBot:Second"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(duplicate.status_code, 409)
        self.client.force_authenticate(self.other_user)
        denied = self.client.get(
            f"/api/v1/integrations/telegram/{connection.id}/",
            **self.tenant(self.other),
        )
        self.assertEqual(denied.status_code, 404)

    def test_webhook_secret_unknown_key_and_duplicate_fail_closed(self):
        connection = self.connect_existing()
        raw = json.dumps(
            {
                "update_id": 44,
                "message": {
                    "message_id": 5,
                    "date": int(timezone.now().timestamp()),
                    "from": {"id": 9001, "is_bot": False, "first_name": "Customer"},
                    "chat": {"id": 9001, "type": "private"},
                    "text": "Hello",
                },
            },
            separators=(",", ":"),
        ).encode()
        url = f"/api/v1/webhooks/telegram/bots/{connection.webhook_public_key}/"
        self.assertEqual(self.client.post(url, raw, content_type="application/json").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/api/v1/webhooks/telegram/bots/unknown/",
                raw,
                content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="bad",
            ).status_code,
            404,
        )
        secret = connection.channel_connection.get_credentials()["webhook_secret"]
        with self.captureOnCommitCallbacks(execute=False):
            first = self.client.post(url, raw, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret)
            second = self.client.post(url, raw, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret)
        self.assertEqual(first.data["accepted"], 1)
        self.assertEqual(second.data["duplicates"], 1)

    def test_private_message_creates_tenant_scoped_crm_records_and_reply(self):
        connection = self.connect_existing()
        response = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/test-event/",
            {
                "event_type": "message",
                "telegram_user_id": 92001,
                "chat_id": 92001,
                "message_id": 77,
                "username": "customer_one",
                "text": "Need a consultation",
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(response.status_code, 202, response.data)
        identity = ContactIdentity.objects.get(
            organization=self.organization,
            channel_connection=connection.channel_connection,
            external_user_id="92001",
        )
        conversation = Conversation.objects.get(contact=identity.contact)
        self.assertEqual(conversation.external_thread_id, "92001")
        message = Message.objects.get(conversation=conversation, direction="inbound")
        self.assertEqual(message.body, "Need a consultation")
        sent, created = send_telegram_message(
            conversation=conversation,
            body="We can help",
            client_message_id="client-telegram-1",
            membership=self.membership,
        )
        duplicate, created_again = send_telegram_message(
            conversation=conversation,
            body="We can help",
            client_message_id="client-telegram-1",
            membership=self.membership,
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(sent.id, duplicate.id)
        self.assertEqual(sent.status, MessageStatus.SENT)

    def test_group_updates_are_ignored_and_media_is_normalized(self):
        connection = self.connect_existing()
        group = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/test-event/",
            {"event_type": "message", "chat_type": "group", "text": "ignore"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(group.status_code, 202)
        self.assertEqual(TelegramWebhookEvent.objects.latest("received_at").status, TelegramEventStatus.IGNORED)
        media = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/test-event/",
            {"event_type": "message", "media_type": "photo", "message_id": 202},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(media.status_code, 202)
        message = Message.objects.get(provider_message_id__contains=":202:message")
        self.assertEqual(message.metadata["media"]["type"], "photo")
        self.assertNotIn("raw", str(message.metadata).lower())

    def test_edit_updates_original_without_creating_duplicate_message(self):
        connection = self.connect_existing()
        endpoint = f"/api/v1/integrations/telegram/{connection.id}/test-event/"
        self.client.post(endpoint, {"event_type": "message", "message_id": 303, "text": "Before"}, format="json", **self.tenant())
        count = Message.objects.count()
        self.client.post(endpoint, {"event_type": "edited_message", "message_id": 303, "text": "After"}, format="json", **self.tenant())
        self.assertEqual(Message.objects.count(), count)
        message = Message.objects.get(provider_message_id__contains=":303:message")
        self.assertEqual(message.body, "After")
        self.assertTrue(message.metadata["edited"])

    def test_commands_languages_and_human_handoff(self):
        connection = self.connect_existing()
        endpoint = f"/api/v1/integrations/telegram/{connection.id}/test-event/"
        with patch("crm.services._enqueue_ai_inbound") as enqueue_ai:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(endpoint, {"event_type": "message", "message_id": 401, "text": "/start"}, format="json", **self.tenant())
            enqueue_ai.assert_not_called()
        self.assertTrue(Message.objects.filter(direction="outbound", sender_type="system", body__contains="/human").exists())
        with patch("crm.services._enqueue_ai_inbound") as enqueue_ai:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(endpoint, {"event_type": "message", "message_id": 402, "text": "/human"}, format="json", **self.tenant())
            enqueue_ai.assert_not_called()
        handoff = AIHandoff.objects.get(reason_code="customer_requested_human")
        self.assertEqual(handoff.conversation.organization, self.organization)
        self.assertEqual(handoff.status, "open")

    def test_existing_conversation_can_enter_configured_telegram_autopilot(self):
        connection = self.connect_existing()
        endpoint = f"/api/v1/integrations/telegram/{connection.id}/test-event/"
        self.client.post(
            endpoint,
            {"event_type": "message", "message_id": 450, "text": "Hello"},
            format="json",
            **self.tenant(),
        )
        conversation = Conversation.objects.get(
            channel_connection=connection.channel_connection
        )
        self.assertEqual(conversation.ai_state, ConversationAIState.OFF)
        connection.automation_mode = TelegramAutomationMode.AUTOPILOT
        connection.save(update_fields=["automation_mode", "updated_at"])
        config = ensure_runtime_config(self.organization)
        config.enabled = True
        config.updated_by = self.membership
        config.save(update_fields=["enabled", "updated_by", "updated_at"])
        config.allowed_channel_connections.add(connection.channel_connection)

        updated = set_conversation_ai_state(
            conversation=conversation,
            actor=self.membership,
            state=ConversationAIState.AUTOPILOT_TELEGRAM,
        )

        self.assertEqual(updated.ai_state, ConversationAIState.AUTOPILOT_TELEGRAM)
        self.assertFalse(AIRun.objects.filter(conversation=conversation).exists())

    def test_blocked_token_invalid_and_transient_outbound_are_safe(self):
        connection = self.connect_existing()
        endpoint = f"/api/v1/integrations/telegram/{connection.id}/test-event/"
        self.client.post(endpoint, {"event_type": "message", "message_id": 501, "text": "Hello"}, format="json", **self.tenant())
        conversation = Conversation.objects.get(channel_connection=connection.channel_connection)
        blocked, _ = send_telegram_message(conversation=conversation, body="[telegram-blocked]", client_message_id="blocked", membership=self.membership)
        self.assertEqual(blocked.status, MessageStatus.FAILED)
        conversation.refresh_from_db()
        self.assertEqual(conversation.handoff_reason, "bot_blocked_by_user")
        conversation.handoff_reason = ""
        conversation.save(update_fields=["handoff_reason", "updated_at"])
        invalid, _ = send_telegram_message(conversation=conversation, body="[telegram-token-invalid]", client_message_id="invalid", membership=self.membership)
        self.assertEqual(invalid.error_code, "bot_token_invalid")
        connection.refresh_from_db()
        self.assertEqual(connection.status, "token_invalid")

    def test_health_rotation_access_pause_reconnect_and_disconnect(self):
        connection = self.connect_managed()
        health = connection_health(connection, run_provider=True)
        self.assertTrue(health["provider_reachable"])
        rotated = self.client.post(f"/api/v1/integrations/telegram/{connection.id}/rotate-token/", {}, format="json", **self.tenant())
        self.assertEqual(rotated.status_code, 200, rotated.data)
        self.assertEqual(rotated.data["token_version"], 2)
        access = self.client.post(f"/api/v1/integrations/telegram/{connection.id}/access-settings/", {"access_restricted": True, "permitted_telegram_user_ids": [700002, 700003]}, format="json", **self.tenant())
        self.assertEqual(access.data["permitted_telegram_user_ids"], [700002, 700003])
        for action, expected in [("pause", "paused"), ("reconnect", "connected"), ("disconnect", "disconnected")]:
            response = self.client.post(f"/api/v1/integrations/telegram/{connection.id}/{action}/", {}, format="json", **self.tenant())
            self.assertEqual(response.data["status"], expected)
        connection.refresh_from_db()
        self.assertEqual(connection.channel_connection.status, ChannelStatus.DISCONNECTED)
        self.assertFalse(connection.channel_connection.get_credentials())

    def test_expiry_job_is_bounded_to_pending_records(self):
        response = self.client.post("/api/v1/integrations/telegram/identity/", {}, format="json", **self.tenant())
        link = TelegramUserLink.objects.get(pk=response.data["id"])
        link.expires_at = timezone.now() - timedelta(seconds=1)
        link.save(update_fields=["expires_at"])
        result = expire_pending_requests()
        self.assertEqual(result["links"], 1)
        link.refresh_from_db()
        self.assertEqual(link.status, "expired")

    def test_live_provider_maps_http_errors_without_leaking_payloads(self):
        provider = LiveTelegramProvider()
        with patch("urllib.request.urlopen", side_effect=TimeoutError("secret token")):
            with self.assertRaises(TelegramProviderError) as raised:
                provider._call("write-only-token", "getMe")
        self.assertEqual(raised.exception.code, "provider_temporarily_unavailable")
        self.assertNotIn("write-only-token", str(raised.exception))

    def test_identity_lists_updates_and_validation_endpoints(self):
        self.assertEqual(
            self.client.get(
                "/api/v1/integrations/telegram/identity/", **self.tenant()
            ).data["status"],
            "not_linked",
        )
        self.link_identity()
        self.assertEqual(
            self.client.get(
                "/api/v1/integrations/telegram/managed-requests/", **self.tenant()
            ).status_code,
            200,
        )
        connection = self.connect_managed("EndpointSupportBot")
        invalid = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/access-settings/",
            {
                "access_restricted": True,
                "permitted_telegram_user_ids": [-1],
            },
            format="json",
            **self.tenant(),
        )
        self.assertEqual(invalid.status_code, 400)
        autopilot = self.client.patch(
            f"/api/v1/integrations/telegram/{connection.id}/",
            {"automation_mode": "autopilot"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(autopilot.status_code, 400)
        self.assertEqual(
            self.client.post(
                f"/api/v1/integrations/telegram/{connection.id}/unknown-action/",
                {},
                format="json",
                **self.tenant(),
            ).status_code,
            400,
        )
        revoked = self.client.delete(
            "/api/v1/integrations/telegram/identity/", **self.tenant()
        )
        self.assertEqual(revoked.data["revoked"], 1)

    def test_existing_rotation_requires_same_bot_and_managed_access(self):
        connection = self.connect_existing()
        missing = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/rotate-token/",
            {},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(missing.status_code, 400)
        mismatch = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/rotate-token/",
            {"replacement_token": "test-only-existing:999999:WrongSupportBot:Wrong"},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(mismatch.status_code, 400)
        access = self.client.post(
            f"/api/v1/integrations/telegram/{connection.id}/access-settings/",
            {"access_restricted": True},
            format="json",
            **self.tenant(),
        )
        self.assertEqual(access.status_code, 409)

    def test_manager_and_bot_payload_validation_fail_closed(self):
        manager = self.client.post(
            "/api/v1/webhooks/telegram/manager/",
            {"update_id": 1},
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
        self.assertEqual(manager.status_code, 403)
        connection = self.connect_existing()
        secret = connection.channel_connection.get_credentials()["webhook_secret"]
        invalid = self.client.post(
            f"/api/v1/webhooks/telegram/bots/{connection.webhook_public_key}/",
            b"not-json",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )
        self.assertEqual(invalid.status_code, 400)

    def test_fake_provider_failure_retry_and_task_lifecycle(self):
        connection = self.connect_existing()
        endpoint = f"/api/v1/integrations/telegram/{connection.id}/test-event/"
        self.client.post(
            endpoint,
            {"event_type": "message", "message_id": 9901, "text": "Hello"},
            format="json",
            **self.tenant(),
        )
        conversation = Conversation.objects.get(
            channel_connection=connection.channel_connection
        )
        with patch("telegram.tasks.retry_telegram_outbound.apply_async"):
            transient, _ = send_telegram_message(
                conversation=conversation,
                body="[telegram-transient-error]",
                client_message_id="retry-transient",
                membership=self.membership,
            )
        attempt = TelegramOutboundAttempt.objects.get(message=transient)
        self.assertEqual(attempt.status, "queued")
        with patch("telegram.tasks.retry_telegram_outbound.apply_async"):
            result = retry_telegram_outbound(str(attempt.id))
        self.assertEqual(result["status"], "queued")
        attempt.refresh_from_db()
        attempt.attempt_count = 3
        attempt.save(update_fields=["attempt_count", "updated_at"])
        exhausted = retry_telegram_outbound(str(attempt.id))
        self.assertEqual(exhausted["status"], "dead_letter")
        self.assertEqual(check_telegram_connections()["checked"], 1)
        self.assertEqual(expire_telegram_requests()["requests"], 0)

    def test_task_wrappers_schedule_failed_events_only(self):
        failed = SimpleNamespace(id="event-id", status="failed", attempt_count=2)
        with (
            patch("telegram.tasks.process_manager_event", return_value=failed),
            patch(
                "telegram.tasks.process_telegram_manager_event.apply_async"
            ) as manager_retry,
        ):
            self.assertEqual(process_telegram_manager_event("event-id"), "event-id")
            manager_retry.assert_called_once()
        with (
            patch("telegram.tasks.process_webhook_event", return_value=failed),
            patch("telegram.tasks.process_telegram_webhook.apply_async") as webhook_retry,
        ):
            self.assertEqual(process_telegram_webhook("event-id"), "event-id")
            webhook_retry.assert_called_once()

    def test_live_provider_official_method_contracts(self):
        provider = LiveTelegramProvider()
        with patch.object(provider, "_call") as call:
            call.return_value = {
                "id": 5001,
                "is_bot": True,
                "username": "OfficialSupportBot",
                "first_name": "Official Support",
                "can_manage_bots": True,
            }
            snapshot = provider.validate_existing_bot("server-only-token")
            self.assertEqual(snapshot.user_id, 5001)
            self.assertTrue(snapshot.can_manage_bots)
            self.assertTrue(provider.manager_health()["can_manage_bots"])
        with patch.object(
            provider,
            "_call",
            side_effect=[
                "managed-server-token",
                {
                    "id": 5002,
                    "is_bot": True,
                    "username": "ManagedOfficialBot",
                    "first_name": "Managed",
                },
            ],
        ) as call:
            managed = provider.get_managed_bot(5002, rotate=True)
            self.assertEqual(managed.user_id, 5002)
            self.assertEqual(call.call_args_list[0].args[1], "replaceManagedBotToken")
        connection = self.connect_existing(bot_id=5003, username="LiveContractBot")
        with override_settings(
            TELEGRAM_MANAGER_BOT_TOKEN="manager-server-token",
            TELEGRAM_BOT_WEBHOOK_BASE_URL="https://api.example.test/webhooks/telegram/bots",
        ):
            with patch.object(provider, "_call", return_value={}) as call:
                configured = provider.configure_bot(connection, "bot-token", "secret")
                self.assertEqual(configured["commands"], ["en", "ru", "uz"])
                self.assertGreaterEqual(call.call_count, 5)
            with patch.object(provider, "_call", return_value={"message_id": 77}):
                self.assertEqual(
                    provider.send_text(
                        connection=connection,
                        chat_id="123",
                        text="Hello",
                        reply_to_message_id="9",
                    ).message_id,
                    "77",
                )
            with patch.object(
                provider,
                "_call",
                side_effect=[
                    {"id": 5003},
                    {
                        "url": f"https://api.example.test/webhooks/telegram/bots/{connection.webhook_public_key}/",
                        "pending_update_count": 2,
                    },
                ],
            ):
                self.assertTrue(provider.health(connection)["webhook_matches"])
            with patch.object(
                provider,
                "_call",
                return_value={
                    "is_access_restricted": True,
                    "added_users": [{"id": 5}],
                },
            ):
                self.assertEqual(provider.get_access_settings(connection)["added_user_ids"], [5])
                self.assertTrue(provider.set_access_settings(connection, True, [5])["is_access_restricted"])

    def test_live_provider_http_and_api_error_mapping(self):
        provider = LiveTelegramProvider()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"ok": True, "result": {"id": 1}}
        ).encode()
        with patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(provider._call("token", "getMe"), {"id": 1})
        http_error = urllib.error.HTTPError(
            "https://api.telegram.org", 429, "rate", {}, BytesIO(b"{}")
        )
        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(TelegramProviderError) as raised:
                provider._call("token", "getMe")
        self.assertEqual(raised.exception.code, "provider_rate_limited")
        for payload, code in [
            ({"ok": False, "error_code": 401}, "bot_token_invalid"),
            ({"ok": False, "description": "bot was blocked"}, "bot_blocked_by_user"),
            ({"ok": False, "description": "chat not found"}, "chat_not_found"),
            ({"ok": False, "error_code": 400}, "provider_request_rejected"),
        ]:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            with patch("urllib.request.urlopen", return_value=response):
                with self.assertRaises(TelegramProviderError) as raised:
                    provider._call("token", "method")
            self.assertEqual(raised.exception.code, code)

    def test_abstract_provider_is_an_explicit_contract(self):
        provider = BaseTelegramProvider()
        for operation in [
            lambda: provider.manager_health(),
            lambda: provider.get_managed_bot(1),
            lambda: provider.validate_existing_bot("token"),
            lambda: provider.configure_bot(None, "token", "secret"),
            lambda: provider.send_text(connection=None, chat_id="1", text="x"),
            lambda: provider.health(None),
            lambda: provider.get_access_settings(None),
            lambda: provider.set_access_settings(None, False, []),
        ]:
            with self.assertRaises(NotImplementedError):
                operation()
