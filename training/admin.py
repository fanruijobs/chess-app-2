from django.contrib import admin

from training.models import TrainingAttempt, TrainingSet, TrainingSetItem


@admin.register(TrainingSet)
class TrainingSetAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    list_filter = ("owner",)
    search_fields = ("name",)
    autocomplete_fields = ("owner",)
    list_select_related = ("owner",)


@admin.register(TrainingSetItem)
class TrainingSetItemAdmin(admin.ModelAdmin):
    list_display = ("training_set", "label", "status", "attempts", "last_trained_at")
    list_filter = ("status", "training_set")


@admin.register(TrainingAttempt)
class TrainingAttemptAdmin(admin.ModelAdmin):
    list_display = ("item", "result", "started_at")
    list_filter = ("result",)
