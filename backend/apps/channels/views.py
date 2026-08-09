from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from channels.models import ChannelConnection
from channels.serializers import ChannelConnectionSerializer
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from core.api.pagination import StandardPagination


class ChannelBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_channels"


class ChannelConnectionListCreateView(ChannelBaseView):
    def get(self, request):
        rows = ChannelConnection.objects.for_organization(request.organization).select_related("branch")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(ChannelConnectionSerializer(page, many=True).data)

    def post(self, request):
        serializer = ChannelConnectionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(ChannelConnectionSerializer(instance).data, status=status.HTTP_201_CREATED)


class ChannelConnectionDetailView(ChannelBaseView):
    def get_object(self, request, connection_id):
        return get_object_or_404(
            ChannelConnection.objects.for_organization(request.organization),
            pk=connection_id,
        )

    def get(self, request, connection_id):
        return Response(ChannelConnectionSerializer(self.get_object(request, connection_id)).data)

    def patch(self, request, connection_id):
        instance = self.get_object(request, connection_id)
        serializer = ChannelConnectionSerializer(
            instance,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, connection_id):
        self.get_object(request, connection_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
