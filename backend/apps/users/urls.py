from django.urls import path

from users.views.users import (
    CSRFView,
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    UserDetailView,
    UserListCreateView,
)

urlpatterns = [

    path("auth/csrf/", CSRFView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="auth-change-password"),

    # User management (Administrator-only)
    path("", UserListCreateView.as_view(), name="user-list"),
    path("<uuid:pk>/", UserDetailView.as_view(), name="user-detail"),

]
