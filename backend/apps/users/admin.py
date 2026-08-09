# apps/users/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from .models import User


@admin.register(User)
class CustomUserAdmin(DjangoUserAdmin):
    # Поля, которые показываются в списке
    list_display = (
        "username",
        "email",
        "display_name",
        "is_staff",
        "is_active",
        "completion_percent",
        "avatar_thumb",
        "id",  # UUID
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("username", "email", "first_name", "last_name", "id")
    ordering = ("username",)
    readonly_fields = ("created_at", "updated_at", "avatar_preview", "cover_preview", "completion_percent")

    # Поля при создании пользователя в админке
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "email", "password1", "password2"),
        }),
    )

    # Группы полей на странице редактирования пользователя
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {
            "fields": (
                ("first_name", "last_name"),
                "email",
                ("phone", "organization"),
            )
        }),
        (_("Media"), {
            "fields": ("avatar", "cover", "avatar_preview", "cover_preview", "completion_percent")
        }),
        (_("Permissions"), {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")
        }),
        (_("Important dates"), {
            "fields": ("last_login", "date_joined", "created_at", "updated_at")
        }),
    )

    # Превью аватара/cover
    def avatar_thumb(self, obj: User):
        if obj.avatar:
            return format_html('<img src="{}" style="height:32px;width:32px;border-radius:50%;" />', obj.avatar.url)
        return "-"
    avatar_thumb.short_description = "Avatar"

    def avatar_preview(self, obj: User):
        if obj.avatar:
            return format_html('<img src="{}" style="max-width:180px;border-radius:8px;" />', obj.avatar.url)
        return "-"
    avatar_preview.short_description = "Avatar preview"

    def cover_preview(self, obj: User):
        if obj.cover:
            return format_html('<img src="{}" style="max-width:320px;border-radius:8px;" />', obj.cover.url)
        return "-"
    cover_preview.short_description = "Cover preview"
