from django.db import models


class Player(models.Model):
    """A chess.com player whose games we track."""

    username = models.CharField(max_length=32, unique=True)  # stored lowercase
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_archive_url = models.URLField(max_length=200, blank=True)

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return self.username


class Game(models.Model):
    """A single chess.com game. PK is the chess.com uuid (dedupe key)."""

    class Result(models.TextChoices):
        WIN = "win"
        LOSS = "loss"
        DRAW = "draw"

    uuid = models.CharField(max_length=64, primary_key=True)
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="games")
    white_username = models.CharField(max_length=32)
    black_username = models.CharField(max_length=32)
    # Unrated / computer games have no ratings
    white_rating = models.PositiveIntegerField(null=True, blank=True)
    black_rating = models.PositiveIntegerField(null=True, blank=True)
    player_side = models.CharField(max_length=1, choices=[("w", "White"), ("b", "Black")])
    player_result_raw = models.CharField(max_length=32)  # chess.com string ("checkmated", "resigned", ...)
    result = models.CharField(max_length=4, choices=Result.choices, db_index=True)
    rules = models.CharField(max_length=32, default="chess")  # variant gate
    rated = models.BooleanField(default=True)
    time_class = models.CharField(max_length=8, blank=True)  # bullet|blitz|rapid|daily
    time_control = models.CharField(max_length=24, blank=True)
    end_time = models.DateTimeField(db_index=True)
    url = models.URLField(max_length=200)
    pgn = models.TextField()
    analyzed_at = models.DateTimeField(null=True, blank=True)
    last_run = models.ForeignKey(
        "analysis.AnalysisRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-end_time"]

    def __str__(self):
        return f"{self.uuid} ({self.white_username} vs {self.black_username})"


class MoveEval(models.Model):
    """Engine evaluation of the position after one ply, from the tracked
    player's perspective. One row per ply — the full eval curve for the graph."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="evals")
    ply = models.PositiveSmallIntegerField()  # 1-based; ply p = board after move p
    san = models.CharField(max_length=16)
    fen = models.CharField(max_length=128)  # after the move
    side_to_move = models.CharField(max_length=1)  # side that just moved ("w"/"b")
    eval_cp = models.IntegerField(null=True, blank=True)  # NULL when mate score
    eval_mate = models.SmallIntegerField(null=True, blank=True)  # signed moves-to-mate

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "ply"], name="uniq_game_ply"),
        ]
        ordering = ["ply"]

    def __str__(self):
        return f"{self.game_id} ply {self.ply}"
