"""Training-set construction and marking. No solution lines are stored —
analysis happens in the browser (Stockfish WASM) and the user is the judge."""

from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Cast
from django.db.models import IntegerField
from django.utils import timezone

from analysis.models import TurningPoint
from books.models import Book, Puzzle
from chessdata.models import Player
from training.models import TrainingAttempt, TrainingSet, TrainingSetItem


def sets_for(user):
    """The sets `user` may see and train: their own; superusers see all.

    The single source of truth for set visibility — views, lookups and the
    service layer all go through here, so `owner=` appears nowhere else.
    """
    queryset = TrainingSet.objects.all()
    if user.is_superuser:
        return queryset
    return queryset.filter(owner=user)


def set_for(user, pk):
    """One visible set by pk, or None. Rejects non-numeric input, which
    arrives straight from POST/query parameters (`clone_from`, `from_set`)."""
    pk = str(pk)
    if not pk.isdigit():
        return None
    return sets_for(user).filter(pk=pk).first()


def _book_queryset(book, filters):
    """Puzzles for a book source, with page and/or number ranges applied.

    `numbers` takes an explicit list of loose numbers (used for hand-picked
    sets, where the numbers are scattered rather than a range); it matches the
    stored `number` text exactly, so it works for plain and positional formats.

    All statuses are eligible — the user judges each position, so the
    verified/review distinction does not gate training sets."""
    puzzles = Puzzle.objects.filter(book=book)
    if filters.get("pages_from"):
        puzzles = puzzles.filter(page__page_number__gte=int(filters["pages_from"]))
    if filters.get("pages_to"):
        puzzles = puzzles.filter(page__page_number__lte=int(filters["pages_to"]))
    numbers = filters.get("numbers")
    if numbers:
        puzzles = puzzles.filter(number__in=[str(n) for n in numbers])
    numbers_from = filters.get("numbers_from")
    numbers_to = filters.get("numbers_to")
    profile = getattr(book, "calibration", None)
    slots_numbering = bool(
        profile
        and (profile.data or {}).get("numbering", {}).get("mode") == "slots"
    )
    if numbers_from or numbers_to:
        if slots_numbering:
            # zero-padded positional numbers ("p03-01") sort lexicographically
            if numbers_from:
                puzzles = puzzles.filter(number__gte=str(numbers_from))
            if numbers_to:
                puzzles = puzzles.filter(number__lte=str(numbers_to))
        else:
            puzzles = puzzles.annotate(
                number_int=Cast("number", output_field=IntegerField())
            )
            if numbers_from:
                puzzles = puzzles.filter(number_int__gte=int(numbers_from))
            if numbers_to:
                puzzles = puzzles.filter(number_int__lte=int(numbers_to))
    return puzzles.order_by("page__page_number", "diagram_index", "id")


def _entry_queryset(entry: dict):
    """Items spec for one source entry (books or turning points)."""
    if entry["source_type"] == "book":
        book = Book.objects.filter(slug=entry.get("book_slug")).first()
        if book is None:
            raise ValueError(f"unknown book slug {entry.get('book_slug')!r}")
        for puzzle in _book_queryset(book, entry):
            yield puzzle, None
    else:
        player = Player.objects.filter(username=entry.get("player")).first()
        if player is None:
            raise ValueError(f"unknown player {entry.get('player')!r}")
        tps = TurningPoint.objects.filter(game__player=player).select_related("game")
        if entry.get("date_from"):
            tps = tps.filter(game__end_time__date__gte=entry["date_from"])
        if entry.get("date_to"):
            tps = tps.filter(game__end_time__date__lte=entry["date_to"])
        if entry.get("classifications"):
            tps = tps.filter(classification__in=entry["classifications"])
        for tp in tps.order_by("game__end_time", "ply"):
            yield None, tp


def _append_entries(training_set: TrainingSet, entries: list[dict], start_order: int,
                    owner):
    """Append the items of the given source entries (order continues).

    `owner` is the acting user, used to resolve cross-set source entries —
    `from_set` comes straight from POST, so it must be scoped like any other
    lookup."""
    order = start_order
    for entry in entries:
        if entry["source_type"] == "rework":
            # the Re-work category of another set — copy the SNAPSHOTS
            source_set = set_for(owner, entry.get("from_set"))
            if source_set is None:
                raise ValueError(f"unknown source set {entry.get('from_set')!r}")
            rework = source_set.items.filter(
                status=TrainingSetItem.Status.REWORK
            ).select_related("puzzle", "turning_point")
            for src in rework:
                TrainingSetItem.objects.create(
                    training_set=training_set, puzzle=src.puzzle,
                    turning_point=src.turning_point, order=order,
                    fen=src.fen, label=src.label, san_best=src.san_best,
                )
                order += 1
            continue
        for puzzle, tp in _entry_queryset(entry):
            if puzzle is not None:
                TrainingSetItem.objects.create(
                    training_set=training_set, puzzle=puzzle, order=order,
                    fen=puzzle.fen, label=puzzle.number or f"#{puzzle.pk}",
                )
            else:
                TrainingSetItem.objects.create(
                    training_set=training_set, turning_point=tp, order=order,
                    fen=tp.fen_before, label=tp.move_number,
                    san_best=tp.san_best,
                )
            order += 1
    return order


def create_set(name: str, entries: list[dict], owner) -> TrainingSet:
    """Create a set from one or more source entries (book ranges, turning
    points and re-work positions from other sets can all be mixed) and
    snapshot its items (FENs copied at creation). `owner` is the acting user."""
    training_set = TrainingSet.objects.create(
        name=name, filters=entries, owner=owner
    )
    try:
        _append_entries(training_set, entries, 0, owner)
    except Exception:
        training_set.delete()
        raise
    return training_set


def add_entries(training_set: TrainingSet, entries: list[dict], owner) -> int:
    """Append more positions to an existing set; returns the new count."""
    max_order = training_set.items.aggregate(max_order=Max("order"))["max_order"]
    start = (max_order + 1) if max_order is not None else 0
    return _append_entries(training_set, entries, start, owner) - start


def clone_set(name: str, source: TrainingSet, owner) -> TrainingSet:
    """Copy an existing set into a new one: item snapshots are copied as-is,
    statuses reset to pending (it's a fresh set to train). The clone belongs
    to the acting user, not to the source's owner."""
    training_set = TrainingSet.objects.create(
        name=name, filters=source.filters, owner=owner
    )
    try:
        for src in (
            source.items.select_related("puzzle", "turning_point")
            .order_by("order", "id")
        ):
            TrainingSetItem.objects.create(
                training_set=training_set, puzzle=src.puzzle,
                turning_point=src.turning_point, order=src.order,
                fen=src.fen, label=src.label, san_best=src.san_best,
            )
    except Exception:
        training_set.delete()
        raise
    return training_set


def mark_item(item: TrainingSetItem, result: str) -> TrainingSetItem:
    """Mark one item (pass / re-work) and bump counters atomically."""
    with transaction.atomic():
        item.status = result
        item.attempts += 1
        item.last_trained_at = timezone.now()
        item.save(update_fields=["status", "attempts", "last_trained_at"])
        TrainingAttempt.objects.create(
            item=item, result=result, finished_at=timezone.now()
        )
        puzzle = item.puzzle
        if puzzle is not None:
            puzzle.attempts += 1
            if result == TrainingSetItem.Status.PASSED:
                puzzle.solved += 1
            else:
                puzzle.failed += 1
            puzzle.last_trained_at = item.last_trained_at
            puzzle.save(
                update_fields=["attempts", "solved", "failed",
                               "last_trained_at", "updated_at"]
            )
    return item


def mark_all(training_set: TrainingSet, result: str,
             status: str = TrainingSetItem.Status.PENDING) -> int:
    """Mark every item of the given status (default: pending); returns count."""
    count = 0
    for item in training_set.items.filter(status=status):
        mark_item(item, result)
        count += 1
    return count


def next_item(training_set: TrainingSet,
              status: str = TrainingSetItem.Status.PENDING) -> TrainingSetItem | None:
    """First item of the given category (pending / rework / passed)."""
    return training_set.items.filter(status=status).first()
