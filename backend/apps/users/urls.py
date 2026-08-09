from django.urls import path

from users.views.users import (
    CSRFView,
    ChangePasswordView,
    LoginView,
    RegistrationView,
    InvitationAcceptView,
    InvitationInspectView,
    LogoutView,
    MeView,
    RefreshView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    UserDetailView,
    UserListCreateView,
)

urlpatterns = [

    path("auth/csrf/", CSRFView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/register/", RegistrationView.as_view(), name="auth-register"),
    path("auth/refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth-change-password"),
    path("auth/invitations/inspect/", InvitationInspectView.as_view(), name="invitation-inspect"),
    path("auth/invitations/accept/", InvitationAcceptView.as_view(), name="invitation-accept"),
    path("auth/password-reset/request/", PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),

    # User management (Administrator-only)
    path("", UserListCreateView.as_view(), name="user-list"),
    path("<uuid:pk>/", UserDetailView.as_view(), name="user-detail"),

]
