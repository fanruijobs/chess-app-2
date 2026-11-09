from django.contrib import admin

from chessdata.models import Game, MoveEval, Player


class MoveEvalInline(admin.TabularInline):
    model = MoveEval
    extra = 0
    fields = ("ply", "san", "side_to_move", "eval_cp", "eval_mate", "fen")
    readonly_fields = fields


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("username", "last_synced_at", "game_count")
    search_fields = ("username",)

    @admin.display(description="games")
    def game_count(self, obj):
        return obj.games.count()


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "uuid",
        "player",
        "white_username",
        "black_username",
        "result",
        "time_class",
        "end_time",
        "analyzed_at",
    )
    list_filter = ("result", "rules", "time_class", "rated", "analyzed_at")
    search_fields = ("uuid", "white_username", "black_username")
    readonly_fields = ("uuid", "pgn", "url")
    inlines = [MoveEvalInline]
