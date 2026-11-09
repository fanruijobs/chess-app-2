import io

import numpy as np
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from books.models import Book, CalibrationProfile
from books.tests.synthetic import fen_to_page

pytestmark = pytest.mark.django_db

CALIBRATION_FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


def _true_labels_from_table(table: str, fen: str) -> list[str]:
    """Derive ground-truth labels from the calibrate --dry-run table.

    Each cluster row lists example squares; the FEN gives the true piece
    on each square, so labels are computed instead of hardcoded.
    """
    import chess

    board = chess.Board(fen)
    labels = []
    for line in table.splitlines():
        if not line.strip().startswith("cluster"):
            continue
        cluster_id = line.split()[1].rstrip(":")
        examples = line.split("examples=")[1].split(" label=")[0]
        square = examples.split(",")[0].strip()
        piece = board.piece_at(chess.parse_square(square))
        code = ("w" if piece.color else "b") + piece.symbol().upper()
        labels.append(f"{cluster_id}={code}")
    return labels


@pytest.fixture
def calibration_page(tmp_path):
    path = tmp_path / "page.png"
    import cv2

    cv2.imwrite(str(path), fen_to_page(CALIBRATION_FEN))
    return str(path)


def test_calibrate_book_saves_ready_profile(tmp_path, calibration_page):
    book = Book.objects.create(title="Test Book", slug="test-book")
    out = io.StringIO()
    call_command(
        "calibrate_book", "test-book", calibration_page, "--dry-run", stdout=out
    )
    labels = _true_labels_from_table(out.getvalue(), CALIBRATION_FEN)
    assert labels, "no clusters in the dry-run table"
    call_command(
        "calibrate_book", "test-book", calibration_page,
        *[f"--label={l}" for l in labels],
    )
    book.refresh_from_db()
    assert book.calibration_status == Book.Calibration.READY
    profile = book.calibration.data
    assert profile["templates"]
    assert all("@" in key for key in profile["templates"])


def test_calibrate_book_requires_labels(calibration_page):
    book = Book.objects.create(title="Test Book 2", slug="test-book-2")
    with pytest.raises(CommandError):
        call_command("calibrate_book", "test-book-2", calibration_page)


def test_calibrate_book_dry_run_writes_nothing(tmp_path, calibration_page):
    book = Book.objects.create(title="Test Book 3", slug="test-book-3")
    out = io.StringIO()
    call_command(
        "calibrate_book", "test-book-3", calibration_page,
        "--dry-run", *[f"--label={l}" for l in _true_labels_from_table(
            _dry_run_table("test-book-3", calibration_page), CALIBRATION_FEN)],
    )
    book.refresh_from_db()
    assert book.calibration_status == Book.Calibration.NONE
    assert not CalibrationProfile.objects.filter(book=book).exists()


def _dry_run_table(book_slug, page_path) -> str:
    out = io.StringIO()
    call_command("calibrate_book", book_slug, page_path, "--dry-run", stdout=out)
    return out.getvalue()


def test_calibrate_book_unknown_book(calibration_page):
    with pytest.raises(CommandError):
        call_command("calibrate_book", "no-such-book", calibration_page)
