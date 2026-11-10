"""Training services: set building (mixed sources), ranges, marking, analysis."""

import datetime

import chess
import pytest
from django.utils import timezone

from analysis.models import AnalysisRun, TurningPoint
from books.models import Book, CalibrationProfile, PageImage, Puzzle
from chessdata.models import Game, Player
from training.models import TrainingSet, TrainingSetItem
from training.services import (
    add_entries,
    create_set,
    mark_all,
    mark_item,
    next_item,
)

pytestmark = pytest.mark.django_db

FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"


@pytest.fixture
def book():
    return Book.objects.create(title="Polgar 5334", slug="polgar-5334")


@pytest.fixture
def puzzles(book):
    p16 = PageImage.objects.create(book=book, page_number=16, image="x.png")
    p17 = PageImage.objects.create(book=book, page_number=17, image="y.png")
    p18 = PageImage.objects.create(book=book, page_number=18, image="z.png")
    return [
        Puzzle.objects.create(book=book, page=p16, diagram_index=1, number="1",
                              fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0),
        Puzzle.objects.create(book=book, page=p17, diagram_index=1, number="7",
                              fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0),
        Puzzle.objects.create(book=book, page=p18, diagram_index=1, number="13",
                              fen=FEN, status=Puzzle.Status.VERIFIED, confidence=1.0),
        Puzzle.objects.create(book=book, page=p18, diagram_index=2, number="14",
                              fen=FEN, status=Puzzle.Status.EXTRACTED, confidence=0.9),
    ]


def book_entry(book, **ranges):
    return {"source_type": "book", "book_slug": book.slug, **ranges}


def test_create_book_set_with_page_range(book, puzzles, owner):
    s = create_set("A", [book_entry(book, pages_from=17, pages_to=18)], owner)
    # all statuses are eligible — the verified/review gate was removed
    assert list(s.items.values_list("label", flat=True)) == ["7", "13", "14"]
    assert s.items.first().fen == FEN


def test_create_book_set_with_number_range(book, puzzles, owner):
    s = create_set("B", [book_entry(book, numbers_from=7, numbers_to=13)], owner)
    assert list(s.items.values_list("label", flat=True)) == ["7", "13"]


def test_create_book_set_with_explicit_numbers(book, puzzles, owner):
    # hand-picked, scattered numbers (the shape used by re-work sets)
    s = create_set("N", [book_entry(book, numbers=[13, 1, 14])], owner)
    assert list(s.items.values_list("label", flat=True)) == ["1", "13", "14"]
    assert s.filters == [book_entry(book, numbers=[13, 1, 14])]


def test_explicit_numbers_ignore_unknown_values(book, puzzles, owner):
    s = create_set("N", [book_entry(book, numbers=[999, 7])], owner)
    assert list(s.items.values_list("label", flat=True)) == ["7"]


def test_create_book_set_slots_number_range(book, puzzles, owner):
    CalibrationProfile.objects.create(
        book=book,
        data={"numbering": {"mode": "slots", "format": "p{page:03d}-{slot:02d}"}},
    )
    for p, number in zip(puzzles, ["p016-01", "p017-01", "p018-01", "p018-02"]):
        p.number = number
        p.save(update_fields=["number"])
    s = create_set(
        "D", [book_entry(book, numbers_from="p017-01", numbers_to="p018-01")], owner
    )
    assert list(s.items.values_list("label", flat=True)) == ["p017-01", "p018-01"]


def _make_turning_points():
    player = Player.objects.create(username="alice")
    game = Game.objects.create(
        uuid="g1", player=player, white_username="alice", black_username="bob",
        player_side="w", player_result_raw="resigned", result="loss",
        time_class="rapid", end_time=timezone.make_aware(datetime.datetime(2026, 8, 1)),
        url="http://x", pgn="1. e4 e5",
    )
    run = AnalysisRun.objects.create(
        game=game, engine_name="Stockfish", engine_version="16",
        depth=18, threshold_cp=150, status=AnalysisRun.RunStatus.DONE,
    )
    tp = TurningPoint.objects.create(
        game=game, run=run, ply=20, move_number="10... Ne5", san_played="Ne5",
        san_best="Qh5", fen_before=FEN, fen_after=FEN, drop_cp=200,
        classification=TurningPoint.Classification.HUNG_PIECE, rule_text="x",
    )
    return player, tp


def test_create_turning_point_set_with_date_range(owner):
    player, tp = _make_turning_points()
    s = create_set("TP", [{
        "source_type": "turning_points", "player": "alice",
        "date_from": "2026-07-01", "date_to": "2026-08-31",
    }], owner)
    assert s.items.count() == 1
    item = s.items.first()
    assert item.turning_point_id == tp.id
    assert item.fen == FEN and item.san_best == "Qh5" and item.label == "10... Ne5"


def test_create_set_mixes_books_and_turning_points(book, puzzles, owner):
    player, tp = _make_turning_points()
    s = create_set("MIX", [
        book_entry(book, pages_from=16, pages_to=16),
        {"source_type": "turning_points", "player": "alice"},
    ], owner)
    labels = list(s.items.values_list("label", flat=True))
    assert labels == ["1", "10... Ne5"]  # book entry first, then the TP entry
    assert s.items.count() == 2
    assert s.filters == [book_entry(book, pages_from=16, pages_to=16),
                         {"source_type": "turning_points", "player": "alice"}]


def test_create_set_rejects_unknown_book(book, owner):
    with pytest.raises(ValueError, match="unknown book"):
        create_set("X", [book_entry(book, book_slug="nope")], owner)


def test_create_set_from_rework_category(book, puzzles, owner):
    source = create_set("SRC", [book_entry(book, pages_from=16, pages_to=16)], owner)
    first = source.items.first()
    mark_item(first, TrainingSetItem.Status.REWORK)
    s = create_set("RW", [{"source_type": "rework", "from_set": source.pk}], owner)
    assert s.items.count() == 1
    item = s.items.first()
    assert item.fen == first.fen and item.label == first.label
    assert item.puzzle_id == first.puzzle_id  # keeps the puzzle link
    assert item.status == TrainingSetItem.Status.PENDING  # fresh start


def test_add_entries_appends_to_existing_set(book, puzzles, owner):
    s = create_set("A", [book_entry(book, pages_from=16, pages_to=16)], owner)
    assert s.items.count() == 1
    added = add_entries(s, [book_entry(book, pages_from=17, pages_to=18)], owner)
    assert added == 3
    assert s.items.count() == 4
    assert [i.order for i in s.items.all()] == [0, 1, 2, 3]
    assert list(s.items.values_list("label", flat=True)) == ["1", "7", "13", "14"]


def test_add_entries_rework_source(book, puzzles, owner):
    source = create_set("SRC", [book_entry(book)], owner)
    for item in source.items.all():
        mark_item(item, TrainingSetItem.Status.REWORK)
    s = create_set("T", [book_entry(book, pages_from=16, pages_to=16)], owner)
    added = add_entries(s, [{"source_type": "rework", "from_set": source.pk}], owner)
    assert added == source.items.count()


def test_mark_item_updates_item_and_puzzle(book, puzzles, owner):
    s = create_set("M", [book_entry(book)], owner)
    item = s.items.first()
    mark_item(item, TrainingSetItem.Status.PASSED)
    item.refresh_from_db()
    assert item.status == TrainingSetItem.Status.PASSED and item.attempts == 1
    assert item.attempt_records.count() == 1
    puzzle = item.puzzle
    puzzle.refresh_from_db()
    assert puzzle.solved == 1 and puzzle.attempts == 1

    mark_item(item, TrainingSetItem.Status.REWORK)
    puzzle.refresh_from_db()
    assert puzzle.failed == 1 and puzzle.attempts == 2
    assert item.status == TrainingSetItem.Status.REWORK  # the Re-work category


def test_mark_all_and_next_item(book, puzzles, owner):
    s = create_set("N", [book_entry(book)], owner)
    first = next_item(s)
    assert first.order == 0
    assert mark_all(s, TrainingSetItem.Status.REWORK) == s.items.count()
    assert next_item(s) is None
    # the re-work category now holds them all
    assert next_item(s, TrainingSetItem.Status.REWORK) is not None
    assert mark_all(s, TrainingSetItem.Status.PASSED,
                    status=TrainingSetItem.Status.REWORK) == s.items.count()
