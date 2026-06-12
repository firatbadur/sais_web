"""Dashboard modelleri için admin kayıtları."""
from __future__ import annotations

from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "doc_type", "original_name", "file_size", "uploaded_by", "created_at")
    list_filter = ("doc_type", "created_at")
    search_fields = ("title", "description", "original_name")
    readonly_fields = ("file_size", "content_type", "original_name", "created_at")
    autocomplete_fields = ()
    ordering = ("-created_at",)
