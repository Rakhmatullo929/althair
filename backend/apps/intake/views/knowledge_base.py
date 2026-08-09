"""
Knowledge Base search API.

GET /api/v1/intake/knowledge-base/
  ?q=<search term>  — full-text search across topic, question, answer, tags
  ?topic=<topic>    — filter by topic label

Returns active entries that best match the query.
Used by the frontend to display prepared answers and by the AI layer
to find relevant responses before making an LLM call.
"""

import logging

from django.db.models import Q
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from intake.models import KnowledgeBaseEntry
from organizations.permissions import OrganizationContextMixin, IsOrganizationMember, HasOrganizationRole

logger = logging.getLogger(__name__)


class KnowledgeBaseEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseEntry
        fields = ['id', 'topic', 'question', 'answer', 'tags', 'updated_at']


class KnowledgeBaseSearchView(OrganizationContextMixin, APIView):
    """
    GET /api/v1/intake/knowledge-base/
    Returns matching active knowledge base entries.
    """
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]

    def get(self, request):
        qs = KnowledgeBaseEntry.objects.filter(
            organization=request.organization, is_active=True,
        )

        topic = request.query_params.get('topic', '').strip()
        if topic:
            qs = qs.filter(topic__icontains=topic)

        q = request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(topic__icontains=q)
                | Q(question__icontains=q)
                | Q(answer__icontains=q)
                | Q(tags__icontains=q)
            ).distinct()

        # Limit to 20 results — this is a lookup helper, not a paginated list
        results = qs.order_by('topic', 'question')[:20]
        return Response({
            'count': results.count(),
            'results': KnowledgeBaseEntrySerializer(results, many=True).data,
        })
