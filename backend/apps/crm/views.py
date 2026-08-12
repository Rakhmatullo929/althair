from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from assistant_context.models import OrganizationAssistantProfile
from channels.models import ChannelConnection
from core.api.pagination import StandardPagination
from crm.models import (
    AssignmentState,
    Contact,
    ContactIdentity,
    ContactNote,
    ContactStatus,
    Conversation,
    ConversationStatus,
    CrmActivity,
    FollowUpTask,
    FollowUpTaskStatus,
    Lead,
    LeadStatus,
    Message,
    Pipeline,
    PipelineStage,
    PipelineStageType,
    Tag,
)
from crm.serializers import (
    ContactIdentitySerializer,
    ContactNoteSerializer,
    ContactSerializer,
    ConversationSerializer,
    CrmActivitySerializer,
    FollowUpTaskSerializer,
    LeadSerializer,
    MessageSerializer,
    PipelineSerializer,
    PipelineStageSerializer,
    TagSerializer,
    model_validation,
)
from crm.services import (
    CrmConflict,
    ProviderUnavailable,
    add_internal_note,
    add_system_message,
    create_contact,
    create_test_conversation,
    ensure_default_pipeline,
    merge_contacts,
    move_lead,
    record_activity,
    send_outbound_message,
)
from organizations.models import (
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationStatus,
)
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current CRM state."
    default_code = "crm_conflict"
    machine_code = "crm_conflict"


class ProviderNotConnected(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Sending is unavailable until this provider is connected."
    default_code = "provider_not_connected"
    machine_code = "provider_not_connected"


class CrmBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "operate"


class ManageCrmBaseView(CrmBaseView):
    write_action = "manage_crm"


def paginate(request, view, queryset, serializer_class, *, context=None):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    serializer_context = {"request": request, **(context or {})}
    return paginator.get_paginated_response(serializer_class(page, many=True, context=serializer_context).data)


def parse_bool(raw):
    return str(raw).lower() in {"1", "true", "yes"}


class ContactListCreateView(CrmBaseView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "crm_search"

    def get(self, request):
        rows = Contact.objects.for_organization(request.organization).prefetch_related("identities", "tags")
        query = request.query_params.get("search", "").strip()
        if query:
            rows = rows.filter(
                Q(display_name__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(company_name__icontains=query)
                | Q(identities__normalized_value__icontains=query)
                | Q(identities__raw_value__icontains=query)
            ).distinct()
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        if value := request.query_params.get("language"):
            rows = rows.filter(preferred_language=value)
        if value := request.query_params.get("tag"):
            rows = rows.filter(tags__id=value)
        if value := request.query_params.get("created_from"):
            rows = rows.filter(created_at__date__gte=value)
        if value := request.query_params.get("created_to"):
            rows = rows.filter(created_at__date__lte=value)
        return paginate(request, self, rows, ContactSerializer)

    def post(self, request):
        data = request.data.copy()
        identities = data.pop("identities", [])
        tag_ids = data.pop("tag_ids", [])
        allowed = {
            key: data[key]
            for key in (
                "display_name", "first_name", "last_name", "company_name", "preferred_language",
                "timezone", "notes_summary", "status",
            )
            if key in data
        }
        if not allowed.get("display_name"):
            return Response({"display_name": ["This field is required."]}, status=400)
        try:
            contact = create_contact(
                organization=request.organization,
                membership=request.organization_membership,
                **allowed,
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        for identity_data in identities:
            serializer = ContactIdentitySerializer(
                data=identity_data,
                context={"request": request, "contact": contact},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
        tags = Tag.objects.for_organization(request.organization).filter(pk__in=tag_ids)
        contact.tags.add(*tags, through_defaults={"organization": request.organization})
        return Response(
            ContactSerializer(contact, context={"request": request, "include_duplicates": True}).data,
            status=status.HTTP_201_CREATED,
        )


class ContactDetailView(CrmBaseView):
    def get_object(self, request, contact_id):
        return get_object_or_404(
            Contact.objects.for_organization(request.organization).prefetch_related("identities", "tags"),
            pk=contact_id,
        )

    def get(self, request, contact_id):
        return Response(ContactSerializer(
            self.get_object(request, contact_id),
            context={"request": request, "include_duplicates": True},
        ).data)

    def patch(self, request, contact_id):
        contact = self.get_object(request, contact_id)
        serializer = ContactSerializer(contact, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        contact = serializer.save()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="contact.archived" if contact.status == ContactStatus.ARCHIVED else "contact.updated",
            summary="Contact archived" if contact.status == ContactStatus.ARCHIVED else "Contact updated",
            contact=contact,
        )
        return Response(ContactSerializer(contact, context={"request": request, "include_duplicates": True}).data)


class ContactMergeView(ManageCrmBaseView):
    def post(self, request, contact_id):
        source = get_object_or_404(Contact.objects.for_organization(request.organization), pk=contact_id)
        target = get_object_or_404(
            Contact.objects.for_organization(request.organization),
            pk=request.data.get("surviving_contact_id"),
        )
        try:
            result = merge_contacts(
                organization=request.organization,
                source=source,
                target=target,
                membership=request.organization_membership,
            )
        except CrmConflict as exc:
            raise Conflict(str(exc)) from exc
        return Response(ContactSerializer(result, context={"request": request, "include_duplicates": True}).data)


class ContactIdentityListView(CrmBaseView):
    def get_contact(self, request, contact_id):
        return get_object_or_404(Contact.objects.for_organization(request.organization), pk=contact_id)

    def get(self, request, contact_id):
        contact = self.get_contact(request, contact_id)
        return Response(ContactIdentitySerializer(contact.identities.all(), many=True).data)

    def post(self, request, contact_id):
        contact = self.get_contact(request, contact_id)
        serializer = ContactIdentitySerializer(data=request.data, context={"request": request, "contact": contact})
        serializer.is_valid(raise_exception=True)
        try:
            identity = serializer.save()
        except Exception as exc:
            if getattr(exc, "get_codes", lambda: None)() == {"raw_value": ["conflict"]}:
                raise Conflict("This identity already belongs to another contact.") from exc
            raise
        return Response(ContactIdentitySerializer(identity).data, status=201)


class ContactIdentityDetailView(CrmBaseView):
    def get_object(self, request, contact_id, identity_id):
        return get_object_or_404(
            ContactIdentity.objects.for_organization(request.organization),
            pk=identity_id,
            contact_id=contact_id,
        )

    def patch(self, request, contact_id, identity_id):
        instance = self.get_object(request, contact_id, identity_id)
        serializer = ContactIdentitySerializer(instance, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError as exc:
            raise Conflict("This identity already belongs to another contact.") from exc
        return Response(serializer.data)

    def delete(self, request, contact_id, identity_id):
        instance = self.get_object(request, contact_id, identity_id)
        contact = instance.contact
        instance.delete()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="identity.removed",
            summary="Contact identity removed",
            contact=contact,
        )
        return Response(status=204)


class ContactNotesView(CrmBaseView):
    def get_contact(self, request, contact_id):
        return get_object_or_404(Contact.objects.for_organization(request.organization), pk=contact_id)

    def get(self, request, contact_id):
        contact = self.get_contact(request, contact_id)
        rows = contact.notes.select_related("author_membership__user")
        return paginate(request, self, rows, ContactNoteSerializer)

    def post(self, request, contact_id):
        contact = self.get_contact(request, contact_id)
        serializer = ContactNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = ContactNote(
            organization=request.organization,
            contact=contact,
            author_membership=request.organization_membership,
            body=serializer.validated_data["body"],
        )
        model_validation(note)
        note.save()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="contact.note_added",
            summary="Contact note added",
            contact=contact,
        )
        return Response(ContactNoteSerializer(note).data, status=201)


class TagListCreateView(CrmBaseView):
    def get(self, request):
        return paginate(request, self, Tag.objects.for_organization(request.organization), TagSerializer)

    def post(self, request):
        serializer = TagSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError as exc:
            raise Conflict("A tag with this name already exists.") from exc
        return Response(serializer.data, status=201)


class ConversationListView(CrmBaseView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "crm_search"

    def get(self, request):
        latest_body = Message.objects.filter(conversation_id=OuterRef("pk")).order_by("-occurred_at").values("body")[:1]
        rows = Conversation.objects.for_organization(request.organization).select_related(
            "contact", "channel_connection", "assigned_membership__user"
        ).annotate(last_message_preview=Subquery(latest_body))
        params = request.query_params
        if parse_bool(params.get("unread")):
            rows = rows.filter(unread_count__gt=0)
        if parse_bool(params.get("unassigned")):
            rows = rows.filter(assigned_membership__isnull=True)
        if parse_bool(params.get("assigned_to_me")):
            rows = rows.filter(assigned_membership=request.organization_membership)
        if value := params.get("assigned_member"):
            rows = rows.filter(assigned_membership_id=value)
        if value := params.get("status"):
            rows = rows.filter(status=value)
        if value := params.get("priority"):
            rows = rows.filter(priority=value)
        if value := params.get("channel_type"):
            rows = rows.filter(channel_type=value)
        if value := params.get("tag"):
            rows = rows.filter(contact__tags__id=value)
        if value := params.get("from"):
            rows = rows.filter(last_message_at__date__gte=value)
        if value := params.get("to"):
            rows = rows.filter(last_message_at__date__lte=value)
        if query := params.get("search", "").strip():
            rows = rows.filter(
                Q(contact__display_name__icontains=query)
                | Q(contact__company_name__icontains=query)
                | Q(subject__icontains=query)
                | Q(messages__body__icontains=query)
            ).distinct()
        return paginate(request, self, rows, ConversationSerializer)


class ConversationDetailView(CrmBaseView):
    def get_object(self, request, conversation_id):
        return get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related(
                "contact", "channel_connection", "assigned_membership__user"
            ),
            pk=conversation_id,
        )

    def get(self, request, conversation_id):
        return Response(ConversationSerializer(self.get_object(request, conversation_id)).data)

    def patch(self, request, conversation_id):
        conversation = self.get_object(request, conversation_id)
        allowed = {key: request.data[key] for key in ("priority", "automation_state", "handoff_reason", "subject") if key in request.data}
        serializer = ConversationSerializer(conversation, data=allowed, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        add_system_message(
            conversation=conversation,
            membership=request.organization_membership,
            body="Conversation settings updated",
            event_type="conversation.updated",
        )
        return Response(ConversationSerializer(conversation).data)


class MessageCursorPagination(CursorPagination):
    page_size = 50
    ordering = "-occurred_at"


class ConversationMessagesView(CrmBaseView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "crm_message"

    def get_conversation(self, request, conversation_id):
        return get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related("contact", "channel_connection"),
            pk=conversation_id,
        )

    def get(self, request, conversation_id):
        conversation = self.get_conversation(request, conversation_id)
        rows = conversation.messages.select_related("sender_membership__user", "conversation__contact")
        paginator = MessageCursorPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(MessageSerializer(page, many=True).data)

    def post(self, request, conversation_id):
        conversation = self.get_conversation(request, conversation_id)
        body = str(request.data.get("body", "")).strip()
        client_id = str(request.data.get("client_message_id", "")).strip()
        cc = request.data.get("cc", [])
        if not isinstance(cc, list):
            return Response({"cc": ["Cc must be a list of email addresses."]}, status=400)
        if not body or not client_id:
            return Response({"body": ["Body and client_message_id are required."]}, status=400)
        try:
            message, created = send_outbound_message(
                organization=request.organization,
                conversation=conversation,
                membership=request.organization_membership,
                body=body,
                client_message_id=client_id,
                human_agent=bool(request.data.get("human_agent", False)),
                cc=cc,
                confirm_segments=bool(request.data.get("confirm_segments", False)),
            )
        except ProviderUnavailable as exc:
            raise ProviderNotConnected(str(exc)) from exc
        except ImportError as exc:
            raise ProviderNotConnected("Provider integration is unavailable.") from exc
        except Exception as exc:
            from instagram.services import InstagramError

            if isinstance(exc, InstagramError):
                error = ProviderNotConnected(exc.code)
                error.status_code = exc.status_code
                error.machine_code = exc.code
                raise error from exc
            from telegram.services import TelegramError

            if isinstance(exc, TelegramError):
                error = ProviderNotConnected(exc.code)
                error.status_code = exc.status_code
                error.machine_code = exc.code
                raise error from exc
            from gmail_integration.services import GmailError

            if isinstance(exc, GmailError):
                error = ProviderNotConnected(exc.code)
                error.status_code = exc.status_code
                error.machine_code = exc.code
                raise error from exc
            from sms.services import SMSError

            if isinstance(exc, SMSError):
                error = ProviderNotConnected(exc.code)
                error.status_code = exc.status_code
                error.machine_code = exc.code
                raise error from exc
            raise
        return Response(MessageSerializer(message).data, status=201 if created else 200)


class ConversationMarkReadView(CrmBaseView):
    def post(self, request, conversation_id):
        with transaction.atomic():
            conversation = get_object_or_404(
                Conversation.objects.select_for_update().for_organization(request.organization), pk=conversation_id
            )
            if conversation.unread_count:
                conversation.unread_count = 0
                conversation.save(update_fields=["unread_count", "updated_at"])
        return Response({"unread_count": 0})


class ConversationAssignView(CrmBaseView):
    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related("contact", "channel_connection"),
            pk=conversation_id,
        )
        target_id = request.data.get("membership_id")
        target = None
        if target_id:
            target = get_object_or_404(
                OrganizationMembership.objects.filter(
                    organization=request.organization,
                    status=OrganizationMembershipStatus.ACTIVE,
                ),
                pk=target_id,
            )
        actor = request.organization_membership
        if actor.role == OrganizationMembershipRole.AGENT and target not in {None, actor}:
            raise PermissionDenied("Agents may only claim a conversation for themselves.")
        if actor.role == OrganizationMembershipRole.AGENT and target is None and conversation.assigned_membership_id not in {None, actor.id}:
            raise PermissionDenied("Agents may only unassign their own conversations.")
        conversation.assigned_membership = target
        conversation.assignment_state = AssignmentState.ASSIGNED if target else AssignmentState.UNASSIGNED
        conversation.full_clean()
        conversation.save(update_fields=["assigned_membership", "assignment_state", "updated_at"])
        add_system_message(
            conversation=conversation,
            membership=actor,
            body="Conversation assigned" if target else "Conversation unassigned",
            event_type="conversation.assigned" if target else "conversation.unassigned",
        )
        return Response(ConversationSerializer(conversation).data)


class ConversationResolveView(CrmBaseView):
    status_value = ConversationStatus.RESOLVED
    event_type = "conversation.resolved"
    event_body = "Conversation resolved"

    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related("contact", "channel_connection"),
            pk=conversation_id,
        )
        conversation.status = self.status_value
        conversation.resolved_at = timezone.now() if self.status_value == ConversationStatus.RESOLVED else None
        conversation.save(update_fields=["status", "resolved_at", "updated_at"])
        add_system_message(
            conversation=conversation,
            membership=request.organization_membership,
            body=self.event_body,
            event_type=self.event_type,
        )
        return Response(ConversationSerializer(conversation).data)


class ConversationReopenView(ConversationResolveView):
    status_value = ConversationStatus.OPEN
    event_type = "conversation.reopened"
    event_body = "Conversation reopened"


class ConversationNotesView(CrmBaseView):
    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related("contact", "channel_connection"),
            pk=conversation_id,
        )
        body = str(request.data.get("body", "")).strip()
        if not body:
            return Response({"body": ["This field is required."]}, status=400)
        message = add_internal_note(conversation=conversation, membership=request.organization_membership, body=body)
        return Response(MessageSerializer(message).data, status=201)


class PipelineListCreateView(ManageCrmBaseView):
    def get(self, request):
        ensure_default_pipeline(request.organization)
        rows = Pipeline.objects.for_organization(request.organization).prefetch_related("stages")
        return Response(PipelineSerializer(rows, many=True).data)

    def post(self, request):
        serializer = PipelineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pipeline = Pipeline(organization=request.organization, **serializer.validated_data)
        model_validation(pipeline)
        try:
            pipeline.save()
        except IntegrityError as exc:
            raise Conflict("The pipeline name or default selection already exists.") from exc
        return Response(PipelineSerializer(pipeline).data, status=201)


class PipelineDetailView(ManageCrmBaseView):
    def get_object(self, request, pipeline_id):
        return get_object_or_404(Pipeline.objects.for_organization(request.organization), pk=pipeline_id)

    def get(self, request, pipeline_id):
        return Response(PipelineSerializer(self.get_object(request, pipeline_id)).data)

    def patch(self, request, pipeline_id):
        pipeline = self.get_object(request, pipeline_id)
        serializer = PipelineSerializer(pipeline, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(pipeline, key, value)
        model_validation(pipeline)
        pipeline.save()
        return Response(PipelineSerializer(pipeline).data)


class PipelineStagesView(ManageCrmBaseView):
    def get_pipeline(self, request, pipeline_id):
        return get_object_or_404(Pipeline.objects.for_organization(request.organization), pk=pipeline_id)

    def get(self, request, pipeline_id):
        return Response(PipelineStageSerializer(self.get_pipeline(request, pipeline_id).stages.all(), many=True).data)

    def post(self, request, pipeline_id):
        pipeline = self.get_pipeline(request, pipeline_id)
        serializer = PipelineStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = PipelineStage(organization=request.organization, pipeline=pipeline, **serializer.validated_data)
        model_validation(stage)
        try:
            stage.save()
        except IntegrityError as exc:
            raise Conflict("A stage with this name or position already exists.") from exc
        return Response(PipelineStageSerializer(stage).data, status=201)


class PipelineStageDetailView(ManageCrmBaseView):
    def patch(self, request, stage_id):
        stage = get_object_or_404(PipelineStage.objects.for_organization(request.organization), pk=stage_id)
        serializer = PipelineStageSerializer(stage, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError as exc:
            raise Conflict("A stage with this name or position already exists.") from exc
        return Response(serializer.data)


def scoped_relationship(request, model, raw_id):
    if not raw_id:
        return None
    queryset = model.objects.all()
    if hasattr(queryset, "for_organization"):
        queryset = queryset.for_organization(request.organization)
    else:
        queryset = queryset.filter(organization=request.organization)
    return get_object_or_404(queryset, pk=raw_id)


class LeadListCreateView(ManageCrmBaseView):
    def get(self, request):
        rows = Lead.objects.for_organization(request.organization).select_related(
            "contact", "pipeline", "stage", "assigned_membership__user"
        )
        params = request.query_params
        for param, field in (
            ("pipeline", "pipeline_id"), ("stage", "stage_id"), ("assigned_member", "assigned_membership_id"),
            ("source_channel", "source_channel_type"), ("status", "status"),
        ):
            if value := params.get(param):
                rows = rows.filter(**{field: value})
        if value := params.get("follow_up_before"):
            rows = rows.filter(next_follow_up_at__lte=value)
        if query := params.get("search", "").strip():
            rows = rows.filter(Q(title__icontains=query) | Q(contact__display_name__icontains=query))
        return paginate(request, self, rows, LeadSerializer)

    def post(self, request):
        data = request.data.copy()
        confirm_duplicate = parse_bool(data.pop("confirm_duplicate", False))
        contact = scoped_relationship(request, Contact, data.get("contact"))
        conversation = scoped_relationship(request, Conversation, data.get("source_conversation"))
        pipeline = scoped_relationship(request, Pipeline, data.get("pipeline")) if data.get("pipeline") else ensure_default_pipeline(request.organization)
        stage = scoped_relationship(request, PipelineStage, data.get("stage")) if data.get("stage") else pipeline.stages.filter(stage_type=PipelineStageType.OPEN).first()
        if conversation and conversation.contact_id != contact.id:
            return Response({"source_conversation": ["Conversation belongs to another contact."]}, status=400)
        duplicates = Lead.objects.for_organization(request.organization).filter(contact=contact, status=LeadStatus.OPEN)
        if duplicates.exists() and not confirm_duplicate:
            raise Conflict("An active lead already exists for this contact. Resubmit with confirm_duplicate=true.")
        data["pipeline"] = str(pipeline.id)
        data["stage"] = str(stage.id)
        if data.get("assigned_membership"):
            scoped_relationship(request, OrganizationMembership, data["assigned_membership"])
        serializer = LeadSerializer(data=data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        lead = Lead(
            organization=request.organization,
            created_by=request.organization_membership,
            updated_by=request.organization_membership,
            source_channel_type=conversation.channel_type if conversation else "",
            status=LeadStatus.OPEN,
            **serializer.validated_data,
        )
        model_validation(lead)
        lead.save()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="lead.created",
            summary="Lead created",
            contact=contact,
            conversation=conversation,
            lead=lead,
        )
        if conversation:
            add_system_message(
                conversation=conversation,
                membership=request.organization_membership,
                body="Lead created from conversation",
                event_type="conversation.lead_created",
            )
        return Response(LeadSerializer(lead).data, status=201)


class LeadDetailView(ManageCrmBaseView):
    def get_object(self, request, lead_id):
        return get_object_or_404(
            Lead.objects.for_organization(request.organization).select_related(
                "contact", "pipeline", "stage", "assigned_membership__user"
            ),
            pk=lead_id,
        )

    def get(self, request, lead_id):
        return Response(LeadSerializer(self.get_object(request, lead_id)).data)

    def patch(self, request, lead_id):
        lead = self.get_object(request, lead_id)
        for key, model in (
            ("contact", Contact), ("source_conversation", Conversation), ("pipeline", Pipeline),
            ("stage", PipelineStage), ("assigned_membership", OrganizationMembership),
        ):
            if request.data.get(key):
                scoped_relationship(request, model, request.data[key])
        serializer = LeadSerializer(lead, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class LeadMoveView(ManageCrmBaseView):
    def post(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.for_organization(request.organization), pk=lead_id)
        stage = get_object_or_404(PipelineStage.objects.for_organization(request.organization), pk=request.data.get("stage_id"))
        try:
            lead = move_lead(lead=lead, stage=stage, membership=request.organization_membership)
        except ValueError as exc:
            return Response({"stage_id": [str(exc)]}, status=400)
        return Response(LeadSerializer(lead).data)


class LeadTerminalView(LeadMoveView):
    stage_type = PipelineStageType.WON

    def post(self, request, lead_id):
        lead = get_object_or_404(Lead.objects.for_organization(request.organization), pk=lead_id)
        stage = get_object_or_404(
            PipelineStage.objects.for_organization(request.organization),
            pipeline=lead.pipeline,
            stage_type=self.stage_type,
        )
        if self.stage_type == PipelineStageType.LOST:
            reason = str(request.data.get("lost_reason", "")).strip()
            if not reason:
                return Response({"lost_reason": ["A lost reason is required."]}, status=400)
            lead.lost_reason = reason
            lead.save(update_fields=["lost_reason", "updated_at"])
        return Response(LeadSerializer(move_lead(lead=lead, stage=stage, membership=request.organization_membership)).data)


class LeadWinView(LeadTerminalView):
    stage_type = PipelineStageType.WON


class LeadLoseView(LeadTerminalView):
    stage_type = PipelineStageType.LOST


class FollowUpTaskListCreateView(ManageCrmBaseView):
    def get(self, request):
        rows = FollowUpTask.objects.for_organization(request.organization).select_related(
            "assigned_membership__user", "related_contact", "related_lead", "related_conversation"
        )
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        if value := request.query_params.get("assigned_member"):
            rows = rows.filter(assigned_membership_id=value)
        if value := request.query_params.get("due_before"):
            rows = rows.filter(due_at__lte=value)
        if value := request.query_params.get("due_after"):
            rows = rows.filter(due_at__gte=value)
        return paginate(request, self, rows, FollowUpTaskSerializer)

    def post(self, request):
        for key, model in (
            ("assigned_membership", OrganizationMembership), ("related_contact", Contact),
            ("related_lead", Lead), ("related_conversation", Conversation),
        ):
            if request.data.get(key):
                scoped_relationship(request, model, request.data[key])
        serializer = FollowUpTaskSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        task = FollowUpTask(
            organization=request.organization,
            created_by=request.organization_membership,
            **serializer.validated_data,
        )
        model_validation(task)
        task.save()
        record_activity(
            organization=request.organization,
            actor_membership=request.organization_membership,
            event_type="task.created",
            summary="Follow-up task created",
            contact=task.related_contact,
            conversation=task.related_conversation,
            lead=task.related_lead,
            task=task,
        )
        return Response(FollowUpTaskSerializer(task).data, status=201)


class FollowUpTaskDetailView(ManageCrmBaseView):
    def get_object(self, request, task_id):
        return get_object_or_404(FollowUpTask.objects.for_organization(request.organization), pk=task_id)

    def get(self, request, task_id):
        return Response(FollowUpTaskSerializer(self.get_object(request, task_id)).data)

    def patch(self, request, task_id):
        task = self.get_object(request, task_id)
        serializer = FollowUpTaskSerializer(task, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        old_status = task.status
        for key, value in serializer.validated_data.items():
            setattr(task, key, value)
        if task.status == FollowUpTaskStatus.COMPLETED:
            task.completed_at = task.completed_at or timezone.now()
        elif old_status == FollowUpTaskStatus.COMPLETED:
            task.completed_at = None
        model_validation(task)
        task.save()
        if task.status != old_status:
            record_activity(
                organization=request.organization,
                actor_membership=request.organization_membership,
                event_type="task.completed" if task.status == FollowUpTaskStatus.COMPLETED else "task.updated",
                summary="Follow-up task completed" if task.status == FollowUpTaskStatus.COMPLETED else "Follow-up task status changed",
                contact=task.related_contact,
                conversation=task.related_conversation,
                lead=task.related_lead,
                task=task,
            )
        return Response(FollowUpTaskSerializer(task).data)


class CrmOverviewView(CrmBaseView):
    def get(self, request):
        now = timezone.now()
        conversations = Conversation.objects.for_organization(request.organization)
        leads = Lead.objects.for_organization(request.organization)
        tasks = FollowUpTask.objects.for_organization(request.organization)
        profile = getattr(request.organization, "profile", None)
        assistant = OrganizationAssistantProfile.objects.filter(organization=request.organization).first()
        stage_counts = list(
            leads.filter(status=LeadStatus.OPEN)
            .values("stage_id", "stage__name", "stage__color_token")
            .annotate(count=Count("id"))
            .order_by("stage__position")
        )
        return Response({
            "open_conversations": conversations.filter(status__in=[ConversationStatus.OPEN, ConversationStatus.PENDING]).count(),
            "unread_conversations": conversations.filter(unread_count__gt=0).count(),
            "unassigned_conversations": conversations.filter(assigned_membership__isnull=True, status__in=[ConversationStatus.OPEN, ConversationStatus.PENDING]).count(),
            "active_contacts": Contact.objects.for_organization(request.organization).filter(status=ContactStatus.ACTIVE).count(),
            "open_leads": leads.filter(status=LeadStatus.OPEN).count(),
            "leads_by_stage": stage_counts,
            "overdue_follow_ups": tasks.filter(status=FollowUpTaskStatus.OPEN, due_at__lt=now).count(),
            "configured_channels": ChannelConnection.objects.for_organization(request.organization).filter(status="active").count(),
            "onboarding_completion_percentage": getattr(profile, "onboarding_completion_percentage", 0),
            "onboarding_completed_at": getattr(profile, "onboarding_completed_at", None),
            "ai_context_status": getattr(assistant, "status", "draft"),
            "ai_context_version": getattr(assistant, "version", 0),
        })


class CrmActivityView(CrmBaseView):
    def get(self, request):
        rows = CrmActivity.objects.for_organization(request.organization).select_related("actor_membership__user")
        for param, field in (
            ("contact", "contact_id"), ("conversation", "conversation_id"),
            ("lead", "lead_id"), ("task", "task_id"), ("event_type", "event_type"),
        ):
            if value := request.query_params.get(param):
                rows = rows.filter(**{field: value})
        return paginate(request, self, rows, CrmActivitySerializer)


class DevTestConversationView(ManageCrmBaseView):
    def post(self, request):
        if request.organization_membership.role not in {
            OrganizationMembershipRole.OWNER,
            OrganizationMembershipRole.ADMIN,
        }:
            raise PermissionDenied("Only an owner or admin can create development test data.")
        try:
            conversation = create_test_conversation(
                organization=request.organization,
                membership=request.organization_membership,
                display_name=str(request.data.get("display_name", "Test customer")).strip(),
                identity_value=str(request.data.get("identity_value", "")).strip(),
                body=str(request.data.get("body", "Hello, I need help.")).strip(),
            )
        except ProviderUnavailable as exc:
            raise PermissionDenied(str(exc)) from exc
        return Response(ConversationSerializer(conversation).data, status=201)
