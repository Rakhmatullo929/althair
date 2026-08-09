import logging

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import (
    IsAuthenticated,
)
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from intake.models import JobEvent, JobNote, JobRecord, TeamLead
from django.contrib.auth import get_user_model
from organizations.models import OrganizationMembership, OrganizationMembershipStatus
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)

from jobs.serializers.jobs import (
    AssignableUserSerializer,
    JobAssignSerializer,
    JobDetailSerializer,
    JobEventSerializer,
    JobListSerializer,
    JobNoteCreateSerializer,
    JobNoteSerializer,
    JobPoRecSerializer,
    JobPriorityUpdateSerializer,
    JobReopenSerializer,
    JobScheduleSerializer,
    JobSeenSerializer,
    JobStatusUpdateSerializer,
    JobUnscheduleSerializer,
    JobUpdateSerializer,
    ManualJobCreateSerializer,
    TeamLeadSerializer,
)
from jobs.services.jobs import (
    add_job_note,
    create_manual_job,
    get_dashboard_summary,
    get_hotlist_queryset,
    get_job_list_queryset,
    mark_ready_to_schedule,
    reopen_job,
    schedule_job,
    set_job_seen,
    unschedule_job,
    update_job_fields,
    update_job_po_rec,
    update_job_priority,
    update_job_status,
)

logger = logging.getLogger(__name__)


class _JobPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 200


class _BaseJobView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = 'manage_crm'
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'jobs'


class JobListView(_BaseJobView):

    def get(self, request):
        qs = get_job_list_queryset(
            request.query_params,
            organization=request.organization,
            user=request.user,
            membership_role=request.organization_membership.role,
        )
        paginator = _JobPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = JobListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class JobHotlistView(_BaseJobView):

    def get(self, request):
        qs = get_hotlist_queryset(
            request.query_params,
            organization=request.organization,
            user=request.user,
            membership_role=request.organization_membership.role,
        )
        paginator = _JobPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = JobListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class DashboardSummaryView(_BaseJobView):

    def get(self, request):
        data = get_dashboard_summary(
            organization=request.organization,
            user=request.user,
            membership_role=request.organization_membership.role,
        )
        return Response(data)


class ManualJobCreateView(_BaseJobView):
    """
    POST /api/v1/jobs/manual/
    Create a staff-entered manual job (no intake interaction required).
    """

    def post(self, request):
        serializer = ManualJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = create_manual_job(
            serializer.validated_data,
            organization=request.organization,
            user=request.user,
        )
        return Response(JobDetailSerializer(job).data, status=status.HTTP_201_CREATED)


class JobDetailView(_BaseJobView):
    """
    GET  /api/v1/jobs/<pk>/  — full job detail including notes + recent history
    """

    def get(self, request, pk):
        job = get_object_or_404(
            JobRecord.objects.select_related('contact', 'source_interaction')
            .prefetch_related('job_notes', 'events'),
            pk=pk, organization=request.organization,
        )
        return Response(JobDetailSerializer(job).data)


class JobUpdateView(_BaseJobView):
    """
    PATCH /api/v1/jobs/<pk>/
    Update job content fields (service details, scope, notes, assignment).
    Status / priority / schedule changes use dedicated action endpoints.
    """

    def patch(self, request, pk):
        job = get_object_or_404(
            JobRecord.objects.select_related('contact'),
            pk=pk,
            organization=request.organization,
        )
        serializer = JobUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        job = update_job_fields(job, serializer.validated_data, user=request.user)
        return Response(JobDetailSerializer(job).data)


class JobStatusUpdateView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/status/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = update_job_status(
            job,
            new_status=serializer.validated_data['status'],
            user=request.user,
            note=serializer.validated_data.get('note', ''),
        )
        return Response(JobDetailSerializer(job).data)


class JobPriorityUpdateView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/priority/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobPriorityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = update_job_priority(
            job, serializer.validated_data['priority'], user=request.user,
        )
        return Response(JobDetailSerializer(job).data)


class JobScheduleView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/schedule/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        job = schedule_job(
            job,
            scheduled_date=d['scheduled_date'],
            scheduled_time=d.get('scheduled_time'),
            assigned_to=d.get('assigned_to', ''),
            assignment_notes=d.get('assignment_notes', ''),
            user=request.user,
        )
        return Response(JobDetailSerializer(job).data)


class JobUnscheduleView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/unschedule/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobUnscheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = unschedule_job(
            job, user=request.user, note=serializer.validated_data.get('note', ''),
        )
        return Response(JobDetailSerializer(job).data)


class JobReopenView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/reopen/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = reopen_job(
            job,
            user=request.user,
            reason=serializer.validated_data.get('reason', ''),
        )
        return Response(JobDetailSerializer(job).data)


class JobReadyToScheduleView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/ready-to-schedule/

    Triage a hotlist job into the 'ready to schedule' state: prioritized
    status, unscheduled, and unassigned. Admin-only (writes go through
    IsAdminForWrite on the base view).
    """

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        job = mark_ready_to_schedule(job, user=request.user)
        return Response(JobDetailSerializer(job).data)


class JobPoRecView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/po-rec/"""

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobPoRecSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = update_job_po_rec(job, serializer.validated_data, user=request.user)
        return Response(JobDetailSerializer(job).data)


class TeamLeadListCreateView(_BaseJobView):
    write_action = 'manage_team'
    """
    GET  /api/v1/jobs/team-leads/        — list active team leads (ordered)
    POST /api/v1/jobs/team-leads/        — add a team lead { name }
    """

    def get(self, request):
        qs = TeamLead.objects.filter(organization=request.organization, is_active=True)
        return Response(TeamLeadSerializer(qs, many=True).data)

    def post(self, request):
        serializer = TeamLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        last = TeamLead.objects.filter(organization=request.organization).order_by('-position').first()
        serializer.save(
            organization=request.organization,
            position=(last.position + 1) if last else 0,
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TeamLeadDetailView(_BaseJobView):
    write_action = 'manage_team'
    """
    PATCH  /api/v1/jobs/team-leads/<pk>/  — rename / reorder a team lead
    DELETE /api/v1/jobs/team-leads/<pk>/  — remove a team lead
    """

    def patch(self, request, pk):
        lead = get_object_or_404(TeamLead, pk=pk, organization=request.organization)
        old_name = lead.name
        serializer = TeamLeadSerializer(lead, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        new_name = serializer.validated_data.get('name')

        if new_name and new_name != old_name:
            # Re-assign all jobs that were under the old lead name.
            JobRecord.objects.filter(
                organization=request.organization, assigned_to=old_name,
            ).update(assigned_to=new_name)

            # Sync new name to the linked User.
            if lead.user_id:
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    user = User.objects.get(pk=lead.user_id)
                    parts = new_name.strip().split(' ', 1)
                    user.first_name = parts[0]
                    user.last_name = parts[1] if len(parts) > 1 else ''
                    user.save(update_fields=['first_name', 'last_name'])
                except Exception:
                    pass

        return Response(serializer.data)

    def delete(self, request, pk):
        lead = get_object_or_404(TeamLead, pk=pk, organization=request.organization)
        lead.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class JobSeenView(_BaseJobView):
    """POST /api/v1/jobs/<pk>/seen/  — toggle the 'seen / in the works' flag.

    Allowed for any authenticated user: this is the acknowledgement an
    Operations user gives to signal they've picked up the work, not a change to
    the job's data.
    """

    write_action = 'operate'

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobSeenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = set_job_seen(job, serializer.validated_data['is_seen'], user=request.user)
        return Response(JobDetailSerializer(job).data)


class JobHistoryView(_BaseJobView):
    """GET /api/v1/jobs/<pk>/history/"""

    def get(self, request, pk):
        get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        qs = JobEvent.objects.filter(
            organization=request.organization, job_id=pk,
        ).select_related('created_by').order_by('-created_at')
        paginator = _JobPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = JobEventSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class JobNoteListView(_BaseJobView):
    """
    GET  /api/v1/jobs/<pk>/notes/  — list notes for a job
    POST /api/v1/jobs/<pk>/notes/  — add a note

    Adding notes is explicitly allowed for Operations users — it's the one write
    they're permitted while the rest of the job card stays read-only for them.
    """

    write_action = 'operate'

    def get(self, request, pk):
        get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        qs = JobNote.objects.filter(
            organization=request.organization, job_id=pk,
        ).select_related('created_by').order_by('-created_at')
        paginator = _JobPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = JobNoteSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = add_job_note(
            job,
            body=serializer.validated_data['body'],
            is_follow_up=serializer.validated_data.get('is_follow_up', False),
            user=request.user,
        )
        return Response(JobNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class JobAssignView(_BaseJobView):
    """
    POST /api/v1/jobs/<pk>/assign/  — assign a job to a user or clear the assignment.
    Body: { user_id: "<uuid>" }  or  { user_id: null }  to unassign.
    """

    def post(self, request, pk):
        job = get_object_or_404(JobRecord, pk=pk, organization=request.organization)
        serializer = JobAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = serializer.validated_data.get('user_id')
        if user_id:
            membership = get_object_or_404(
                OrganizationMembership.objects.select_related('user'),
                organization=request.organization,
                user_id=user_id,
                user__is_active=True,
                status=OrganizationMembershipStatus.ACTIVE,
            )
            target_user = membership.user
            old_display = job.assigned_user.get_full_name() if job.assigned_user else ''
            new_display = target_user.get_full_name() or target_user.email or str(target_user)
            job.assigned_user = target_user
        else:
            old_display = job.assigned_user.get_full_name() if job.assigned_user else ''
            new_display = ''
            job.assigned_user = None
        job.save(update_fields=['assigned_user', 'updated_at'])
        created_by = request.user if getattr(request.user, 'is_authenticated', False) else None
        JobEvent.objects.create(
            organization=request.organization,
            job=job,
            event_type='assigned',
            old_value=str(old_display)[:500],
            new_value=str(new_display)[:500],
            created_by=created_by,
        )
        return Response(JobDetailSerializer(job).data)


class AssignableUsersView(_BaseJobView):
    """
    GET /api/v1/jobs/assignable-users/  — list of all active users for the assign dropdown.
    Accessible to any authenticated user (read-only, minimal fields only).
    """

    def get(self, request):
        memberships = OrganizationMembership.objects.filter(
            organization=request.organization,
            status=OrganizationMembershipStatus.ACTIVE,
            user__is_active=True,
        ).select_related('user').order_by(
            'user__first_name', 'user__last_name', 'user__email',
        )
        def _display(u):
            full = u.get_full_name().strip()
            if full:
                return full
            # Show first or last name alone if only one is set
            name = (u.first_name or u.last_name or '').strip()
            if name:
                return name
            # Fall back to username (usually more readable than raw email)
            return u.username or u.email

        data = [
            {
                'id': str(u.id),
                'display_name': _display(u),
                'role': membership.role,
            }
            for membership in memberships
            for u in [membership.user]
        ]
        return Response(AssignableUserSerializer(data, many=True).data)
