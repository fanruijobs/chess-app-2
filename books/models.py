"""Puzzle books: scanned pages -> extracted positions -> training puzzles."""

import io
import logging

import chess
from django.db import models

logger = logging.getLogger("books.cv")


class Book(models.Model):
    """One puzzle book (e.g. Polgar 5334, a Steps workbook, Yusupov)."""

    class Calibration(models.TextChoices):
        NONE = "none", "Not calibrated"
        PENDING = "pending", "Clusters labeled"
        READY = "ready", "Ready"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    calibration_status = models.CharField(
        max_length=12, choices=Calibration.choices, default=Calibration.NONE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class PageImage(models.Model):
    """One scanned page (photo or PDF-rendered page)."""

    class Status(models.TextChoices):
        NEW = "new"
        PROCESSED = "processed"
        FAILED = "failed"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="pages")
    image = models.ImageField(upload_to="books/pages/%Y/%m/")
    thumbnail = models.ImageField(upload_to="books/thumbs/%Y/%m/", blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    source_file = models.CharField(max_length=255, blank=True)  # "page012.jpg" or "book.pdf#page=34"
    width = models.PositiveIntegerField(null=True, blank=True)  # original px (bbox coords)
    height = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    error = models.TextField(blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["book", "page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "page_number"],
                condition=models.Q(page_number__isnull=False),
                name="uniq_book_page_number",
            ),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # generate a thumbnail once after the image is first stored
        if self.image and not self.thumbnail and not getattr(self, "_thumb_done", False):
            self._thumb_done = True
            try:
                from PIL import Image as PILImage

                with self.image.open() as img:
                    pil = PILImage.open(img)
                    pil.thumbnail((400, 400))
                    buf = io.BytesIO()
                    pil.convert("RGB").save(buf, format="JPEG", quality=80)
                name = f"{self.image.name.rsplit('.', 1)[0]}_thumb.jpg"
                from django.core.files.base import ContentFile

                self.thumbnail.save(name, ContentFile(buf.getvalue()), save=False)
                super().save(update_fields=["thumbnail"])
            except Exception as exc:  # noqa: BLE001 - thumbnails are best-effort
                logger.warning("Thumbnail generation failed for page %s: %s", self.pk, exc)

    def __str__(self):
        return f"{self.book.slug} page {self.page_number or self.pk}"


class CalibrationProfile(models.Model):
    """Per-book glyph cluster labels + templates for the CV recognizer.

    data JSON shape:
      {"clusters": {"3": {"label": "wN", "count": 41}, ...},   cluster id -> piece code
       "templates": {"3": [[...24x24 ints...]]},               mean glyph per cluster
       "empty": {"max_ink": 0.04},                             empty-square ink threshold
       "solidity_cut": 0.35,                                   white/black piece cut
       "features": {"means": [...], "stds": [...]},            z-score stats (10 dims)
       "source": {"page_image_id": 12, "diagram_index": 2}}
    """

    book = models.OneToOneField(
        Book, on_delete=models.CASCADE, related_name="calibration"
    )
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"calibration of {self.book.slug}"


class Puzzle(models.Model):
    """One diagram position extracted from a page."""

    class Status(models.TextChoices):
        EXTRACTED = "extracted"
        REVIEW = "review"  # low confidence / invalid FEN
        VERIFIED = "verified"
        REJECTED = "rejected"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="puzzles")
    page = models.ForeignKey(
        PageImage, null=True, blank=True, on_delete=models.SET_NULL, related_name="puzzles"
    )
    diagram_index = models.PositiveSmallIntegerField(null=True, blank=True)
    number = models.CharField(max_length=32, blank=True)  # book's own numbering, verbatim
    fen = models.CharField(max_length=128)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.EXTRACTED, db_index=True
    )
    confidence = models.FloatField(null=True, blank=True)  # 0..1; 1.0 when verified
    diagram_bbox = models.JSONField(null=True, blank=True)  # [x, y, w, h] in original px
    piece_cells = models.JSONField(null=True, blank=True)  # 64 entries: cluster id or None
    extraction_notes = models.TextField(blank=True)

    # training stats (updated by the training app on mark)
    attempts = models.PositiveIntegerField(default=0)
    solved = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    last_trained_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["book", "number", "id"]
        indexes = [models.Index(fields=["book", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "number"],
                condition=~models.Q(number=""),
                name="uniq_book_number",
            ),
            models.UniqueConstraint(
                fields=["book", "page", "diagram_index"],
                condition=models.Q(page__isnull=False) & models.Q(diagram_index__isnull=False),
                name="uniq_book_page_diagram",
            ),
        ]

    def board(self):
        """chess.Board for the stored FEN, or None when invalid."""
        try:
            return chess.Board(self.fen)
        except ValueError:
            return None

    def __str__(self):
        label = self.number or f"#{self.pk}"
        return f"{self.book.slug} {label} ({self.status})"
