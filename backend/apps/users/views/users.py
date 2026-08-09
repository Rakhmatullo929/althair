import logging

from django.contrib.auth import authenticate, get_user_model
from django.middleware import csrf
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle

from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from django.conf import settings

from django.shortcuts import get_object_or_404

from users.serializers.users import (
    ChangePasswordSerializer,
    LoginSerializer,
    UserAdminSerializer,
    UserSerializer,
)
from users.models import UserRole
from intake.models import TeamLead
from organizations.models import (
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
)
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)
from organizations.services import update_membership

User = get_user_model()
security_logger = logging.getLogger('security.audit')


def _user_display_name(user) -> str:
    """Readable display name used as the TeamLead name."""
    full = user.get_full_name().strip()
    if full:
        return full
    partial = (user.first_name or user.last_name or '').strip()
    if partial:
        return partial
    return user.username or user.email or ''


def _sync_team_lead(user) -> None:
    """Create (or link) a TeamLead entry for an active Operations user."""
    if user.role != UserRole.OPERATIONS or not user.is_active:
        return
    name = _user_display_name(user)
    if not name:
        return
    try:
        _do_sync_team_lead(user, name)
    except Exception:
        # Silently skip if migration hasn't been applied yet or any DB error occurs.
        # Fall back to the legacy name-only lookup.
        try:
            if not TeamLead.objects.filter(name=name).exists():
                last = TeamLead.objects.order_by('-position').first()
                TeamLead.objects.create(
                    name=name,
                    position=(last.position + 1) if last else 0,
                )
        except Exception:
            pass


def _rename_team_lead(old_name: str, new_name: str) -> None:
    """Rename an existing TeamLead from old_name to new_name. Never creates a duplicate."""
    try:
        lead = TeamLead.objects.filter(name=old_name).first()
        if lead:
            lead.name = new_name
            lead.save(update_fields=['name'])
    except Exception:
        pass


def _do_sync_team_lead(user, name) -> None:
    """Inner sync that requires the user FK column to exist."""
    # If a TeamLead already linked to this user exists, just keep it in sync.
    try:
        lead = user.team_lead
        if lead.name != name:
            lead.name = name
            lead.save(update_fields=['name'])
        return
    except TeamLead.DoesNotExist:
        pass
    # Try to find an existing lead by name and link it.
    lead = TeamLead.objects.filter(name=name, user__isnull=True).first()
    if lead:
        lead.user = user
        lead.save(update_fields=['user'])
        return
    # Create a new lead and link it.
    last = TeamLead.objects.order_by('-position').first()
    TeamLead.objects.create(
        name=name,
        position=(last.position + 1) if last else 0,
        user=user,
    )


class IsAdminRole(permissions.BasePermission):
    """Allow only Administrator-role users (or Django superusers)."""

    message = 'Administrator access required.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and getattr(user, 'is_admin', False)
        )


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _cookie_opts(max_age: int):
    secure = settings.SIMPLE_JWT.get('AUTH_COOKIE_SECURE', not settings.DEBUG)
    samesite = settings.SIMPLE_JWT.get('AUTH_COOKIE_SAMESITE', 'Lax')
    http_only = settings.SIMPLE_JWT.get('AUTH_COOKIE_HTTP_ONLY', True)
    return dict(max_age=max_age, httponly=http_only, secure=secure,
                samesite=samesite, path='/', domain=None)


def _set_tokens(response: Response, access_token, refresh_token):
    access_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'jwt-auth')
    refresh_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh')
    access_age = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
    refresh_age = int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds())
    response.set_cookie(access_name, str(access_token), **_cookie_opts(access_age))
    response.set_cookie(refresh_name, str(refresh_token), **_cookie_opts(refresh_age))


def _clear_tokens(response: Response):
    access_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'jwt-auth')
    refresh_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh')
    for name in (access_name, refresh_name):
        response.delete_cookie(name, path='/')


class LoginView(APIView):
    """
    POST {email, password}
    Sets HttpOnly cookies (access/refresh), returns user data.
    Rate limited: 5 requests/min.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data['email'].lower().strip()
        password = ser.validated_data['password']
        client_ip = _get_client_ip(request)

        try:
            u = User.objects.get(email__iexact=email)
            username = u.username
        except User.DoesNotExist:
            security_logger.warning(
                'Failed login attempt — unknown email from IP %s', client_ip
            )
            return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        user = authenticate(request, username=username, password=password)
        if not user or not user.is_active:
            security_logger.warning(
                'Failed login attempt for user %s from IP %s', u.id, client_ip
            )
            return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        security_logger.info('Successful login for user %s from IP %s', user.id, client_ip)

        refresh = RefreshToken.for_user(user)
        access = refresh.access_token

        data = UserSerializer(user).data
        resp = Response(data, status=status.HTTP_200_OK)
        _set_tokens(resp, access, refresh)
        return resp


class RefreshView(APIView):
    """
    POST (no body)
    Reads refresh from HttpOnly cookie, blacklists old, issues new pair.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'users'

    def post(self, request):
        refresh_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh')
        raw_refresh = request.COOKIES.get(refresh_name)
        if not raw_refresh:
            return Response({'detail': 'No refresh token.'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            old = RefreshToken(raw_refresh)
            try:
                old.blacklist()
            except Exception:
                pass
            user_id = old.get('user_id')
            user = User.objects.get(id=user_id)
        except (TokenError, User.DoesNotExist):
            return Response({'detail': 'Invalid refresh token.'}, status=status.HTTP_401_UNAUTHORIZED)

        new_refresh = RefreshToken.for_user(user)
        new_access = new_refresh.access_token

        resp = Response({'detail': 'refreshed'}, status=status.HTTP_200_OK)
        _set_tokens(resp, new_access, new_refresh)
        return resp


class LogoutView(APIView):
    """
    POST — blacklist refresh token from cookie, clear both cookies.
    ?all=true — invalidate all refresh tokens for user (logout all devices).
    """
    def post(self, request):
        refresh_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh')
        raw_refresh = request.COOKIES.get(refresh_name)

        if raw_refresh:
            try:
                RefreshToken(raw_refresh).blacklist()
            except Exception:
                pass

        # Logout from all devices
        if request.query_params.get('all', '').lower() == 'true':
            from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
            OutstandingToken.objects.filter(user=request.user).delete()

        security_logger.info(
            'User %s logged out from IP %s', request.user.id, _get_client_ip(request)
        )

        resp = Response({'detail': 'logged out'}, status=status.HTTP_200_OK)
        _clear_tokens(resp)
        return resp


class MeView(APIView):
    """
    GET — current user profile.
    PATCH — update profile fields.
    """
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        ser = UserSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class ChangePasswordView(APIView):
    """
    POST /api/v1/users/auth/change-password/
    { current_password, new_password }
    Verifies the current password, sets the new one, and clears must_change_password.
    """

    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(ser.validated_data['current_password']):
            return Response(
                {'current_password': 'Current password is incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(ser.validated_data['new_password'])
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])
        security_logger.info('User %s changed their password', user.id)
        return Response(UserSerializer(user).data)


class CSRFView(APIView):
    """
    GET — sets csrftoken cookie (double-submit protection).
    """
    permission_classes = [permissions.AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({'csrftoken': csrf.get_token(request)})


# ── User management (Administrator-only) ──────────────────────────────────────

class UserListCreateView(OrganizationContextMixin, APIView):
    """
    GET  /api/v1/users/  — list all users (admin only)
    POST /api/v1/users/  — create a user (admin only)
    """
    permission_classes = [
        permissions.IsAuthenticated,
        IsOrganizationMember,
        HasOrganizationRole,
    ]
    required_action = 'manage_team'

    def get(self, request):
        qs = User.objects.filter(
            organization_memberships__organization=request.organization,
        ).order_by('first_name', 'last_name', 'email')
        return Response(UserAdminSerializer(
            qs,
            many=True,
            context={'organization': request.organization},
        ).data)

    def post(self, request):
        requested_role = request.data.get('membership_role', OrganizationMembershipRole.AGENT)
        if requested_role not in OrganizationMembershipRole.values:
            return Response(
                {'detail': 'Invalid membership role.', 'code': 'invalid_role'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_role == OrganizationMembershipRole.OWNER and request.organization_membership.role != OrganizationMembershipRole.OWNER:
            return Response(
                {'detail': 'Only an owner can grant ownership.', 'code': 'owner_required'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = UserAdminSerializer(
            data=request.data,
            context={'organization': request.organization},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        OrganizationMembership.objects.create(
            organization=request.organization,
            user=user,
            role=requested_role,
            status=OrganizationMembershipStatus.ACTIVE,
            joined_at=timezone.now(),
        )
        security_logger.info(
            'User %s created organization member %s',
            request.user.id, user.id,
        )
        return Response(
            UserAdminSerializer(
                user, context={'organization': request.organization},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class UserDetailView(OrganizationContextMixin, APIView):
    """
    PATCH  /api/v1/users/<pk>/  — update a user's role / active status / profile (admin only)
    DELETE /api/v1/users/<pk>/  — permanently delete a user (admin only, cannot delete self)
    """
    permission_classes = [
        permissions.IsAuthenticated,
        IsOrganizationMember,
        HasOrganizationRole,
    ]
    required_action = 'manage_team'

    def _membership(self, request, pk):
        return get_object_or_404(
            OrganizationMembership.objects.select_related('user'),
            organization=request.organization,
            user_id=pk,
        )

    def patch(self, request, pk):
        membership = self._membership(request, pk)
        target = membership.user
        requested_role = request.data.get('membership_role')
        requested_status = request.data.get('membership_status')
        if requested_role is not None and requested_role not in OrganizationMembershipRole.values:
            return Response(
                {'detail': 'Invalid membership role.', 'code': 'invalid_role'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_status is not None and requested_status not in OrganizationMembershipStatus.values:
            return Response(
                {'detail': 'Invalid membership status.', 'code': 'invalid_status'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if requested_role == OrganizationMembershipRole.OWNER and request.organization_membership.role != OrganizationMembershipRole.OWNER:
            return Response(
                {'detail': 'Only an owner can grant ownership.', 'code': 'owner_required'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = UserAdminSerializer(
            target,
            data=request.data,
            partial=True,
            context={'organization': request.organization},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        try:
            update_membership(
                membership=membership,
                role=requested_role,
                status=requested_status,
            )
        except ValueError as exc:
            return Response(
                {'detail': str(exc), 'code': 'last_owner_required'},
                status=status.HTTP_409_CONFLICT,
            )
        security_logger.info(
            'User %s updated organization member %s',
            request.user.id, user.id,
        )
        return Response(UserAdminSerializer(
            user, context={'organization': request.organization},
        ).data)

    def delete(self, request, pk):
        membership = self._membership(request, pk)
        if str(membership.user_id) == str(request.user.id):
            return Response(
                {'detail': 'You cannot delete your own account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            update_membership(
                membership=membership,
                status=OrganizationMembershipStatus.SUSPENDED,
            )
        except ValueError as exc:
            return Response(
                {'detail': str(exc), 'code': 'last_owner_required'},
                status=status.HTTP_409_CONFLICT,
            )
        security_logger.info('User %s suspended organization member %s', request.user.id, pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
