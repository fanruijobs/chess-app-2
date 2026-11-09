"""Training sets: book puzzles or game turning points, user-marked outcomes.

No solution lines are stored anywhere — the engine analyzes on the fly on the
interactive session board, and the user marks each item pass / work-again.
"""

from django.conf import settings
from django.db import models


class TrainingSet(models.Model):
    """A named collection of training positions.

    Source-agnostic: one set can mix entries from any number of book ranges
    and turning-point ranges. `filters` records the entry specs used to
    build it ([{source_type: "book"|"turning_points", ...range fields}]).

    `owner` is the only access rule: a set is visible to its owner (and to
    superusers). Marks live on the set's items, so they travel with it.
    PROTECT — deleting a user must not silently take their sets with it.
    """

    name = models.CharField(max_length=120)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="training_sets",
    )
    filters = models.JSONField(default=list)  # list of source entry specs
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TrainingSetItem(models.Model):
    """One position snapshot inside a set. The FEN is copied at creation so
    later puzzle edits/re-analysis never change what the user trains on."""

    class Status(models.TextChoices):
        PENDING = "pending"
        PASSED = "passed"
        REWORK = "rework"  # marked as failure — goes to the Re-work category

    training_set = models.ForeignKey(
        TrainingSet, on_delete=models.CASCADE, related_name="items"
    )
    puzzle = models.ForeignKey(
        "books.Puzzle", null=True, blank=True, on_delete=models.CASCADE,
        related_name="training_items",
    )
    turning_point = models.ForeignKey(
        "analysis.TurningPoint", null=True, blank=True, on_delete=models.CASCADE,
        related_name="training_items",
    )
    fen = models.CharField(max_length=128)  # snapshot
    label = models.CharField(max_length=64, blank=True)  # "p03-01" / "23... Nf6"
    san_best = models.CharField(max_length=16, blank=True)  # snapshot
    order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    last_trained_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                name="training_item_single_source",
                condition=(
                    models.Q(puzzle__isnull=False, turning_point__isnull=True)
                    | models.Q(puzzle__isnull=True, turning_point__isnull=False)
                ),
            ),
        ]

    def __str__(self):
        return f"{self.training_set_id} {self.label or self.pk}"


class TrainingAttempt(models.Model):
    """One user-marked session on an item (the user is the judge)."""

    class Result(models.TextChoices):
        PASSED = "passed"
        REWORK = "rework"

    item = models.ForeignKey(
        TrainingSetItem, on_delete=models.CASCADE, related_name="attempt_records"
    )
    result = models.CharField(max_length=8, choices=Result.choices)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.item_id} {self.result}"
