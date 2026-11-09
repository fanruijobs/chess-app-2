from django.contrib import admin

from analysis.models import AnalysisJob, AnalysisRun, TurningPoint, WorkerHeartbeat


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ("game", "status", "depth", "threshold_cp", "priority", "created_at", "finished_at")
    list_filter = ("status", "priority")
    search_fields = ("game__uuid", "game__white_username", "game__black_username")


@admin.register(WorkerHeartbeat)
class WorkerHeartbeatAdmin(admin.ModelAdmin):
    list_display = ("pid", "started_at", "last_beat_at", "current_game")
    readonly_fields = ("pid", "started_at", "last_beat_at", "current_game")


class TurningPointInline(admin.TabularInline):
    model = TurningPoint
    extra = 0
    fields = (
        "move_number", "san_played", "san_best", "drop_cp",
        "classification", "llm_status",
    )
    readonly_fields = fields


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("game", "engine_name", "depth", "threshold_cp", "status", "total_plies", "started_at")
    list_filter = ("status", "engine_name")


@admin.register(TurningPoint)
class TurningPointAdmin(admin.ModelAdmin):
    list_display = (
        "game", "move_number", "san_played", "san_best", "drop_cp",
        "classification", "llm_status",
    )
    list_filter = ("classification", "llm_status")
    search_fields = ("game__uuid", "game__white_username", "game__black_username")
    readonly_fields = (
        "game", "run", "ply", "move_number", "san_played", "san_best",
        "fen_before", "fen_after", "eval_before_cp", "eval_before_mate",
        "eval_after_cp", "eval_after_mate", "drop_cp",
    )
