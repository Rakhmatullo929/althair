import logging
import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import authenticate, get_user_model
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
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
    InvitationAcceptSerializer,
    InvitationTokenSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegistrationSerializer,
    UserAdminSerializer,
    UserSerializer,
)
from users.models import PasswordResetToken, UserRole
from users.utils.authentication import enforce_csrf
from intake.models import TeamLead
from organizations.models import (
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationInvitation,
    OrganizationInvitationStatus,
)
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)
from organizations.services import accept_invitation, hash_invitation_token, update_membership

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


def _auth_response(user, *, status_code=status.HTTP_200_OK, extra=None):
    refresh = RefreshToken.for_user(user)
    payload = UserSerializer(user).data
    if extra:
        payload.update(extra)
    response = Response(payload, status=status_code)
    _set_tokens(response, refresh.access_token, refresh)
    return response


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
        enforce_csrf(request)
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

        from control_plane.models import OrganizationOperationalState

        if OrganizationOperationalState.objects.filter(
            organization__memberships__user=user,
            organization__memberships__status=OrganizationMembershipStatus.ACTIVE,
            new_logins_disabled=True,
        ).exists():
            security_logger.warning('Customer login blocked by an organization operational control')
            return Response(
                {'detail': 'Sign-in is temporarily unavailable for this organization.', 'code': 'operational_control_active'},
                status=status.HTTP_403_FORBIDDEN,
            )

        security_logger.info('Successful login for user %s from IP %s', user.id, client_ip)

        return _auth_response(user)


class RegistrationView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'registration'

    def post(self, request):
        enforce_csrf(request)
        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user, organization = serializer.save()
        except IntegrityError:
            return Response(
                {
                    'detail': 'Registration could not be completed with these details.',
                    'code': 'registration_unavailable',
                },
                status=status.HTTP_409_CONFLICT,
            )
        security_logger.info('Registered user %s with organization %s', user.id, organization.id)
        return _auth_response(
            user,
            status_code=status.HTTP_201_CREATED,
            extra={'organization_id': str(organization.id)},
        )


class RefreshView(APIView):
    """
    POST (no body)
    Reads refresh from HttpOnly cookie, blacklists old, issues new pair.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'users'

    def post(self, request):
        enforce_csrf(request)
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

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_sensitive'

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


class InvitationInspectView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'invitation'

    def post(self, request):
        enforce_csrf(request)
        serializer = InvitationTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = OrganizationInvitation.objects.select_related('organization').filter(
            token_hash=hash_invitation_token(serializer.validated_data['token']),
        ).first()
        if not invitation:
            return Response({'state': 'invalid'}, status=status.HTTP_404_NOT_FOUND)
        if invitation.status == OrganizationInvitationStatus.PENDING and invitation.expires_at <= timezone.now():
            invitation.status = OrganizationInvitationStatus.EXPIRED
            invitation.save(update_fields=['status', 'updated_at'])
        return Response({
            'state': invitation.status,
            'email': invitation.email,
            'organization_name': invitation.organization.name,
            'role': invitation.role,
            'expires_at': invitation.expires_at,
        })


class InvitationAcceptView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'invitation'

    @transaction.atomic
    def post(self, request):
        enforce_csrf(request)
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']
        invitation = OrganizationInvitation.objects.select_for_update().filter(
            token_hash=hash_invitation_token(raw_token),
        ).first()
        if not invitation:
            return Response(
                {'detail': 'Invitation is invalid or already used.', 'code': 'invitation_invalid'},
                status=status.HTTP_409_CONFLICT,
            )
        if invitation.status != OrganizationInvitationStatus.PENDING:
            return Response(
                {'detail': f'Invitation is {invitation.status}.', 'code': f'invitation_{invitation.status}'},
                status=status.HTTP_409_CONFLICT,
            )
        if invitation.expires_at <= timezone.now():
            invitation.status = OrganizationInvitationStatus.EXPIRED
            invitation.save(update_fields=['status', 'updated_at'])
            return Response(
                {'detail': 'Invitation has expired.', 'code': 'invitation_expired'},
                status=status.HTTP_410_GONE,
            )

        user = request.user if request.user.is_authenticated else None
        created_user = False
        if user is None:
            user = User.objects.filter(email__iexact=invitation.email).first()
            if user:
                return Response(
                    {'detail': 'Sign in with the invited account to continue.', 'code': 'login_required'},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            password = serializer.validated_data.get('password')
            if not password:
                return Response(
                    {'detail': 'A password is required for a new account.', 'code': 'password_required'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user = User.objects.create_user(
                username=invitation.email,
                email=invitation.email,
                password=password,
                first_name=serializer.validated_data.get('first_name', ''),
                last_name=serializer.validated_data.get('last_name', ''),
            )
            created_user = True
        try:
            membership = accept_invitation(raw_token=raw_token, user=user)
        except ValueError as exc:
            return Response(
                {'detail': str(exc), 'code': 'invitation_email_mismatch'},
                status=status.HTTP_403_FORBIDDEN,
            )
        security_logger.info(
            'User %s accepted invitation %s for organization %s',
            user.id,
            invitation.id,
            membership.organization_id,
        )
        payload = {
            'detail': 'Invitation accepted.',
            'organization_id': str(membership.organization_id),
            'membership_role': membership.role,
        }
        if created_user:
            return _auth_response(user, status_code=status.HTTP_201_CREATED, extra=payload)
        return Response(payload)


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_sensitive'

    def post(self, request):
        enforce_csrf(request)
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data['email']).first()
        development_reset_url = None
        if user:
            raw_token = secrets.token_urlsafe(32)
            PasswordResetToken.objects.create(
                user=user,
                token_hash=hashlib.sha256(raw_token.encode('utf-8')).hexdigest(),
                expires_at=timezone.now() + timedelta(hours=1),
            )
            client_app_url = getattr(settings, 'CLIENT_APP_URL', 'http://localhost:3001').rstrip('/')
            reset_path = f"/ru/reset-password/{raw_token}"
            if settings.DEBUG:
                development_reset_url = f"{client_app_url}{reset_path}"
                send_mail(
                    'Development password reset',
                    f'Open this development-only reset link: {development_reset_url}',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=True,
                )
        payload = {
            'detail': 'If the account can be reset, recovery instructions are available.',
            'delivery': 'development_console' if settings.DEBUG else 'not_configured',
        }
        if development_reset_url:
            payload['development_reset_url'] = development_reset_url
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_sensitive'

    @transaction.atomic
    def post(self, request):
        enforce_csrf(request)
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_hash = hashlib.sha256(serializer.validated_data['token'].encode('utf-8')).hexdigest()
        reset_token = PasswordResetToken.objects.select_for_update().filter(
            token_hash=token_hash,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).select_related('user').first()
        if not reset_token:
            return Response(
                {'detail': 'Reset token is invalid or expired.', 'code': 'reset_token_invalid'},
                status=status.HTTP_410_GONE,
            )
        reset_token.user.set_password(serializer.validated_data['password'])
        reset_token.user.save(update_fields=['password'])
        reset_token.used_at = timezone.now()
        reset_token.save(update_fields=['used_at'])
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
        OutstandingToken.objects.filter(user=reset_token.user).delete()
        security_logger.info('User %s completed password reset', reset_token.user.id)
        return Response({'detail': 'Password reset complete.'})


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
        authority = {'viewer': 1, 'agent': 2, 'manager': 3, 'admin': 4, 'owner': 5}
        if authority[requested_role] > authority[request.organization_membership.role]:
            return Response(
                {'detail': 'You cannot grant a role above your own.', 'code': 'role_escalation_denied'},
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
        actor_role = request.organization_membership.role
        authority = {'viewer': 1, 'agent': 2, 'manager': 3, 'admin': 4, 'owner': 5}
        if membership.role == OrganizationMembershipRole.OWNER and actor_role != OrganizationMembershipRole.OWNER:
            return Response(
                {'detail': "Only an owner can change another owner's membership.", 'code': 'owner_required'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if requested_role and authority[requested_role] > authority[actor_role]:
            return Response(
                {'detail': 'You cannot grant a role above your own.', 'code': 'role_escalation_denied'},
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
