from django.db import models

# TurningPoint.Classification labels mirror the rule registry in
# analysis/classifiers.py 1:1.


class AnalysisJob(models.Model):
    """A queued analysis request, processed by the analyze_worker daemon."""

    class JobStatus(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"
        CANCELLED = "cancelled"

    game = models.ForeignKey("chessdata.Game", on_delete=models.CASCADE, related_name="jobs")
    depth = models.PositiveSmallIntegerField()
    threshold_cp = models.IntegerField()
    priority = models.SmallIntegerField(default=0)  # higher runs first
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.QUEUED)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-priority", "created_at"]
        constraints = [
            # at most one active (queued/running) job per game
            models.UniqueConstraint(
                fields=["game"],
                condition=models.Q(status__in=["queued", "running"]),
                name="uniq_active_job_per_game",
            ),
        ]

    def __str__(self):
        return f"{self.game_id} [{self.status}] depth {self.depth}"


class WorkerHeartbeat(models.Model):
    """Single-row liveness record of the analyze_worker daemon (pk=1)."""

    pid = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    last_beat_at = models.DateTimeField(null=True, blank=True)
    current_game = models.ForeignKey(
        "chessdata.Game", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        verbose_name = "worker heartbeat"


class AnalysisRun(models.Model):
    """One engine pass over a game. Game.last_run points at the latest."""

    class RunStatus(models.TextChoices):
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    game = models.ForeignKey("chessdata.Game", on_delete=models.CASCADE, related_name="runs")
    engine_name = models.CharField(max_length=64)  # e.g. "Stockfish 18"
    engine_version = models.CharField(max_length=64)
    depth = models.PositiveSmallIntegerField()
    threshold_cp = models.IntegerField()  # recorded job setting (selection rule is fixed)
    status = models.CharField(max_length=8, choices=RunStatus.choices, default=RunStatus.RUNNING)
    total_plies = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.game_id} ({self.engine_name} depth {self.depth})"


class TurningPoint(models.Model):
    """The first user mistake per game that threw away a decent position:
    the eval before the move was equal-or-better (>= -1.5) and dropped by
    more than 2.0 points."""

    class Classification(models.TextChoices):
        ALLOWED_MATE = "allowed_mate", "Allowed mate"
        MISSED_MATE = "missed_mate", "Missed mate"
        HUNG_PIECE = "hung_piece", "Hung a piece"
        WALKED_INTO_CHECK = "walked_into_check", "Walked into check"
        MISSED_CAPTURE = "missed_capture", "Missed capture"
        MISSED_OPPORTUNITY = "missed_opportunity", "Missed opportunity"
        PROMOTION_MISSED = "promotion_missed", "Missed promotion"
        UNSPECIFIED_BLUNDER = "unspecified_blunder", "Unspecified blunder"

    class LlmStatus(models.TextChoices):
        PENDING = "pending"
        DONE = "done"
        FAILED = "failed"

    game = models.ForeignKey("chessdata.Game", on_delete=models.CASCADE, related_name="turning_points")
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="turning_points")
    ply = models.PositiveSmallIntegerField()
    move_number = models.CharField(max_length=12)  # "23... Nf6" or "24. Qh5"
    san_played = models.CharField(max_length=16)
    san_best = models.CharField(max_length=16)
    fen_before = models.CharField(max_length=128)  # decision position (user to move)
    fen_after = models.CharField(max_length=128)  # position after the blunder
    eval_before_cp = models.IntegerField(null=True, blank=True)
    eval_before_mate = models.SmallIntegerField(null=True, blank=True)
    eval_after_cp = models.IntegerField(null=True, blank=True)
    eval_after_mate = models.SmallIntegerField(null=True, blank=True)
    drop_cp = models.IntegerField()  # cp_norm(before) - cp_norm(after)
    classification = models.CharField(max_length=32, choices=Classification.choices)
    rule_text = models.TextField()  # rule-based explanation sentence
    llm_status = models.CharField(max_length=8, choices=LlmStatus.choices, default=LlmStatus.PENDING)
    llm_text = models.TextField(blank=True)  # rendered summary/lesson/fix
    llm_model = models.CharField(max_length=64, blank=True)
    llm_error = models.TextField(blank=True)
    llm_created_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["game", "ply"], name="uniq_tp_game_ply"),
        ]
        ordering = ["ply"]

    def __str__(self):
        return f"{self.game_id} {self.move_number} ({self.classification})"
