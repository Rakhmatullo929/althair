from django.contrib import admin

from organizations.models import (
    Branch,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationProfile,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "industry", "default_language", "created_at")
    list_filter = ("status", "industry", "default_language")
    search_fields = ("name", "slug")


admin.site.register(OrganizationMembership)
admin.site.register(OrganizationInvitation)
admin.site.register(Branch)
admin.site.register(OrganizationProfile)
