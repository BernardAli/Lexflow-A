from django.contrib import admin

from .models import DocumentType, FieldDefinition, MatterType


class FieldInline(admin.TabularInline):
    model = FieldDefinition
    extra = 0


@admin.register(MatterType)
class MatterTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "key", "is_active")
    list_filter = ("tenant",)
    inlines = [FieldInline]


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "matter_type", "weight")
    list_filter = ("tenant",)
