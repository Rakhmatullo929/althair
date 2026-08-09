from django.urls import path

from jobs.views.jobs import (
    AssignableUsersView,
    DashboardSummaryView,
    JobAssignView,
    JobDetailView,
    JobHistoryView,
    JobHotlistView,
    JobListView,
    JobNoteListView,
    JobPoRecView,
    JobPriorityUpdateView,
    JobReadyToScheduleView,
    JobReopenView,
    JobScheduleView,
    JobSeenView,
    JobStatusUpdateView,
    JobUnscheduleView,
    JobUpdateView,
    ManualJobCreateView,
    TeamLeadDetailView,
    TeamLeadListCreateView,
)

app_name = 'jobs'

urlpatterns = [
    path('', JobListView.as_view(), name='job-list'),
    path('hotlist/', JobHotlistView.as_view(), name='job-hotlist'),
    path('dashboard-summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('manual/', ManualJobCreateView.as_view(), name='job-manual-create'),
    path('assignable-users/', AssignableUsersView.as_view(), name='assignable-users'),

    # Team leads (route-calendar daily grouping)
    path('team-leads/', TeamLeadListCreateView.as_view(), name='team-lead-list'),
    path('team-leads/<uuid:pk>/', TeamLeadDetailView.as_view(), name='team-lead-detail'),

    path('<uuid:pk>/', JobDetailView.as_view(), name='job-detail'),
    path('<uuid:pk>/update/', JobUpdateView.as_view(), name='job-update'),

    path('<uuid:pk>/status/', JobStatusUpdateView.as_view(), name='job-status'),
    path('<uuid:pk>/priority/', JobPriorityUpdateView.as_view(), name='job-priority'),
    path('<uuid:pk>/schedule/', JobScheduleView.as_view(), name='job-schedule'),
    path('<uuid:pk>/unschedule/', JobUnscheduleView.as_view(), name='job-unschedule'),
    path('<uuid:pk>/reopen/', JobReopenView.as_view(), name='job-reopen'),
    path('<uuid:pk>/ready-to-schedule/', JobReadyToScheduleView.as_view(), name='job-ready-to-schedule'),
    path('<uuid:pk>/po-rec/', JobPoRecView.as_view(), name='job-po-rec'),
    path('<uuid:pk>/seen/', JobSeenView.as_view(), name='job-seen'),
    path('<uuid:pk>/assign/', JobAssignView.as_view(), name='job-assign'),

    path('<uuid:pk>/history/', JobHistoryView.as_view(), name='job-history'),
    path('<uuid:pk>/notes/', JobNoteListView.as_view(), name='job-notes'),
]
