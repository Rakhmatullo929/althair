"""
Escalation queue API.

Provides the operations dashboard with:
  GET  /api/v1/intake/escalations/            — paginated list of escalated interactions
  GET  /api/v1/intake/escalations/unread-count/ — integer count for the red badge
  POST /api/v1/intake/escalations/<id>/acknowledge/ — mark one as read
  POST /api/v1/intake/escalations/acknowledge-all/  — mark all unread as read
"""

import logging

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from intake.models import Interaction, InteractionCategory
from organizations.permissions import OrganizationContextMixin, IsOrganizationMember, HasOrganizationRole

logger = logging.getLogger(__name__)


class EscalationSerializer(serializers.ModelSerializer):
    contact_name = serializers.SerializerMethodField()
    contact_phone = serializers.SerializerMethodField()
    contact_email = serializers.SerializerMethodField()
    channel_display = serializers.CharField(source='get_channel_display', read_only=True)
    related_job_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Interaction
        fields = [
            'id', 'channel', 'channel_display',
            'caller_phone', 'caller_email',
            'contact_name', 'contact_phone', 'contact_email',
            'escalation_reason', 'is_read',
            'raw_content', 'summary',
            'related_job_id',
            'created_at',
        ]

    def get_contact_name(self, obj):
        return obj.contact.name if obj.contact else ''

    def get_contact_phone(self, obj):
        return obj.contact.phone if obj.contact else ''

    def get_contact_email(self, obj):
        return obj.contact.email if obj.contact else ''


class _TenantEscalationView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]


class EscalationListView(_TenantEscalationView):
    """
    GET  /api/v1/intake/escalations/
    Returns all interactions categorised as escalation, newest first.
    Query params:
      unread=true   — filter to unread only
      channel=sms   — filter by channel
    """
    def get(self, request):
        qs = (
            Interaction.objects
            .filter(
                organization=request.organization,
                category=InteractionCategory.ESCALATION,
            )
            .select_related('contact', 'related_job')
            .order_by('-created_at')
        )

        unread_param = request.query_params.get('unread', '').lower()
        if unread_param in ('true', '1', 'yes'):
            qs = qs.filter(is_read=False)

        channel = request.query_params.get('channel', '').strip()
        if channel:
            qs = qs.filter(channel=channel)

        # Simple pagination
        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = min(100, max(1, int(request.query_params.get('page_size', 25))))
        except ValueError:
            page, page_size = 1, 25

        total = qs.count()
        offset = (page - 1) * page_size
        items = qs[offset: offset + page_size]

        return Response({
            'count': total,
            'page': page,
            'page_size': page_size,
            'results': EscalationSerializer(items, many=True).data,
        })


class EscalationUnreadCountView(_TenantEscalationView):
    """
    GET /api/v1/intake/escalations/unread-count/
    Returns {"unread": <int>} — used for the red badge in the UI.
    """
    def get(self, request):
        count = Interaction.objects.filter(
            organization=request.organization,
            category=InteractionCategory.ESCALATION,
            is_read=False,
        ).count()
        return Response({'unread': count})


class EscalationAcknowledgeView(_TenantEscalationView):
    """
    POST /api/v1/intake/escalations/<id>/acknowledge/
    Marks a single escalation as read.
    """
    write_action = 'operate'

    def post(self, request, pk):
        try:
            interaction = Interaction.objects.get(
                pk=pk,
                organization=request.organization,
                category=InteractionCategory.ESCALATION,
            )
        except Interaction.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not interaction.is_read:
            interaction.is_read = True
            interaction.save(update_fields=['is_read'])
            logger.info(
                'Escalation acknowledged: interaction=%s by user=%s',
                str(interaction.id)[:8],
                request.user.pk if request.user else 'anon',
            )

        return Response(EscalationSerializer(interaction).data)


class EscalationAcknowledgeAllView(_TenantEscalationView):
    """
    POST /api/v1/intake/escalations/acknowledge-all/
    Marks ALL unread escalations as read in one call.
    Returns {"acknowledged": <count>}.
    """
    write_action = 'operate'

    def post(self, request):
        updated = Interaction.objects.filter(
            organization=request.organization,
            category=InteractionCategory.ESCALATION,
            is_read=False,
        ).update(is_read=True)
        logger.info(
            'Bulk acknowledge escalations: count=%d user=%s',
            updated, request.user.pk if request.user else 'anon',
        )
        return Response({'acknowledged': updated})
