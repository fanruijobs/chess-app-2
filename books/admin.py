from django.contrib import admin

from books.models import Book, CalibrationProfile, PageImage, Puzzle


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "calibration_status", "created_at")
    search_fields = ("title", "slug")


@admin.register(PageImage)
class PageImageAdmin(admin.ModelAdmin):
    list_display = ("book", "page_number", "source_file", "status", "extracted_at")
    list_filter = ("book", "status")


@admin.register(CalibrationProfile)
class CalibrationProfileAdmin(admin.ModelAdmin):
    list_display = ("book", "updated_at")
    readonly_fields = ("data",)


@admin.register(Puzzle)
class PuzzleAdmin(admin.ModelAdmin):
    list_display = (
        "book", "number", "status", "confidence",
        "attempts", "solved", "failed",
    )
    list_filter = ("book", "status")
    search_fields = ("number", "fen")
