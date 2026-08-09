from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


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
