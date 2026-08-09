from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant_context.models import OrganizationAssistantProfile
from assistant_context.serializers import AssistantProfileSerializer, AssistantRevisionSerializer
from assistant_context.services import publish_assistant_profile
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from organizations.policies import organization_allows_method


class AssistantContextBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_settings"

    def get_profile(self, request):
        profile = OrganizationAssistantProfile.objects.filter(
            organization=request.organization,
        ).first()
        if profile:
            return profile
        defaults = {
            "supported_languages": [request.organization.default_language],
            "default_language": request.organization.default_language,
            "updated_by": request.user,
        }
        if not organization_allows_method(request.organization.status, "PATCH"):
            return OrganizationAssistantProfile(
                organization=request.organization,
                **defaults,
            )
        return OrganizationAssistantProfile.objects.create(
            organization=request.organization,
            **defaults,
        )


class AssistantContextView(AssistantContextBaseView):
    def get(self, request):
        return Response(AssistantProfileSerializer(self.get_profile(request)).data)

    def patch(self, request):
        profile = self.get_profile(request)
        serializer = AssistantProfileSerializer(
            profile,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AssistantContextPublishView(AssistantContextBaseView):
    def post(self, request):
        try:
            profile, revision = publish_assistant_profile(
                profile=self.get_profile(request),
                actor=request.user,
            )
        except ValueError as exc:
            return Response(
                {
                    "detail": "Required assistant context fields are missing.",
                    "code": "assistant_context_incomplete",
                    "fields": str(exc).split(","),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "profile": AssistantProfileSerializer(profile).data,
                "revision": AssistantRevisionSerializer(revision).data,
            }
        )


class AssistantContextRevisionListView(AssistantContextBaseView):
    def get(self, request):
        profile = self.get_profile(request)
        if profile._state.adding:
            return Response([])
        revisions = profile.revisions.select_related("published_by")[:25]
        return Response(AssistantRevisionSerializer(revisions, many=True).data)
