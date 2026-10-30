from django.contrib import admin

from .models import MappingProfile


@admin.register(MappingProfile)
class MappingProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "version", "adapter_key", "matter_type")
    list_filter = ("tenant", "adapter_key")

    def has_add_permission(self, request):
        return False  # The audited operator command allocates revisions atomically.

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
