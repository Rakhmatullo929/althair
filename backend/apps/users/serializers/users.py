from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from organizations.models import Organization
from organizations.services import create_organization

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class RegistrationSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    organization_name = serializers.CharField(max_length=160)
    industry = serializers.CharField(max_length=30, default="generic")
    default_language = serializers.ChoiceField(choices=["ru", "uz", "en"], default="ru")
    timezone = serializers.CharField(max_length=64, default="Asia/Tashkent")

    def validate_email(self, value):
        return value.strip().lower()

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate(self, attrs):
        # A deliberately generic conflict avoids exposing account existence.
        if User.objects.filter(email__iexact=attrs["email"]).exists():
            raise serializers.ValidationError(
                {"detail": "Registration could not be completed with these details."}
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        organization_name = validated_data.pop("organization_name").strip()
        industry = validated_data.pop("industry")
        default_language = validated_data.pop("default_language")
        organization_timezone = validated_data.pop("timezone")
        password = validated_data.pop("password")
        email = validated_data["email"]
        base_slug = slugify(organization_name)[:68] or "workspace"
        slug = base_slug
        suffix = 2
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base_slug[: max(1, 78 - len(str(suffix)))]}-{suffix}"
            suffix += 1
        user = User.objects.create_user(
            username=email,
            password=password,
            **validated_data,
        )
        organization = create_organization(
            creator=user,
            name=organization_name,
            slug=slug,
            industry=industry,
            default_language=default_language,
            timezone=organization_timezone,
        )
        return user, organization


class InvitationTokenSerializer(serializers.Serializer):
    token = serializers.CharField(write_only=True, min_length=32, max_length=256)


class InvitationAcceptSerializer(InvitationTokenSerializer):
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        required=False,
        allow_blank=False,
    )

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(InvitationTokenSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class UserSerializer(serializers.ModelSerializer):
    completion_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = (
            'id', 'username', 'first_name', 'last_name',
            'email', 'phone', 'avatar', 'cover',
            'date_joined', 'completion_percent',
            'is_active',
            'must_change_password',
        )
        # role / is_admin / is_active are READ-ONLY here so a user can never
        # promote themselves via /auth/me — only admins change them via the
        # user-management API (UserAdminSerializer).
        read_only_fields = (
            'id', 'username', 'date_joined',
            'is_active',
            'must_change_password',
        )


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, min_length=8, trim_whitespace=False)


class UserAdminSerializer(serializers.ModelSerializer):
    """Tenant-safe user fields; membership role/status are resolved separately."""

    full_name = serializers.SerializerMethodField()
    membership_role = serializers.SerializerMethodField()
    membership_status = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, min_length=8)
    # Optional — derived from email on create (see create()).
    username = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = (
            'id', 'username', 'first_name', 'last_name', 'full_name',
            'email', 'phone', 'is_active',
            'membership_role', 'membership_status',
            'date_joined', 'password', 'must_change_password',
        )
        read_only_fields = (
            'id', 'date_joined', 'must_change_password',
            'membership_role', 'membership_status',
        )

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def _membership(self, obj):
        organization = self.context.get('organization')
        if not organization:
            return None
        return obj.organization_memberships.filter(organization=organization).first()

    def get_membership_role(self, obj):
        membership = self._membership(obj)
        return membership.role if membership else None

    def get_membership_status(self, obj):
        membership = self._membership(obj)
        return membership.status if membership else None

    def validate_email(self, value):
        return (value or '').strip().lower()

    def validate(self, data):
        if not self.instance:  # create — password required
            if not (data.get('password') or '').strip():
                raise serializers.ValidationError(
                    {'password': 'A temporary password is required when creating a user.'}
                )
        return data

    def create(self, validated_data):
        password = validated_data.pop('password', '') or ''
        email = validated_data.get('email', '')
        username = (validated_data.get('username') or email or '').strip()
        if not username:
            raise serializers.ValidationError({'email': 'Email or username is required.'})
        if User.objects.filter(username__iexact=username).exists():
            raise serializers.ValidationError({'username': 'A user with that username already exists.'})
        validated_data['username'] = username
        user = User(**validated_data)
        if password:
            user.set_password(password)
            # Admin-created accounts with a temp password must change it on first login.
            user.must_change_password = True
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
