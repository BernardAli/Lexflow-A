from django.contrib import admin

from .models import Membership, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "city", "security_profile", "allow_external_ai", "retention_days")
    readonly_fields = ("uid", "api_token_prefix")
    exclude = ("data_key_wrapped", "api_token_hash")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "title")
    list_filter = ("tenant", "role")
