"""Training sets: create (mixed sources), session (on-the-fly engine lines),
mark, and results by category."""

import io
import json
import logging

import chess
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from analysis.models import TurningPoint
from books.models import Book
from books.views import rotate_position_180
from chessdata.models import Player
from training.models import TrainingAttempt, TrainingSet, TrainingSetItem
from training.pdf import render_set_pdf
from training.services import (
    add_entries,
    clone_set,
    create_set,
    mark_all,
    mark_item,
    next_item,
    set_for,
    sets_for,
)

logger = logging.getLogger("training")

CATEGORIES = (
    (TrainingSetItem.Status.PENDING, "Pending"),
    (TrainingSetItem.Status.REWORK, "Re-work"),
    (TrainingSetItem.Status.PASSED, "Passed"),
)


def _set_or_404(user, pk):
    """The user's own set, or 404. 404 rather than 403 so that another
    user's set never reveals that it exists."""
    return get_object_or_404(sets_for(user), pk=pk)


def _stats(training_set) -> dict:
    counts = training_set.items.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=TrainingSetItem.Status.PENDING)),
        passed=Count("id", filter=Q(status=TrainingSetItem.Status.PASSED)),
        rework=Count("id", filter=Q(status=TrainingSetItem.Status.REWORK)),
    )
    decided = counts["passed"] + counts["rework"]
    counts["pass_rate"] = round(counts["passed"] / decided * 100) if decided else None
    return counts


def _entry_from_post(post, index: str) -> dict:
    """One source entry from the form's indexed fields (source_type_0, ...)."""
    source_type = post.get(f"source_type_{index}", "book")
    entry = {"source_type": source_type}
    if source_type == "rework":
        from_set = (post.get(f"from_set_{index}") or "").strip()
        if from_set.isdigit():
            entry["from_set"] = int(from_set)
        return entry
    if source_type == "book":
        entry["book_slug"] = (post.get(f"book_{index}") or "").strip()
        for key in ("pages_from", "pages_to", "numbers_from", "numbers_to"):
            value = (post.get(f"{key}_{index}") or "").strip()
            if value:
                entry[key] = value
    else:
        entry["player"] = (post.get(f"player_{index}") or "").strip().lower()
        for key in ("date_from", "date_to"):
            value = (post.get(f"{key}_{index}") or "").strip()
            if value:
                entry[key] = value
        if post.get(f"classifications_{index}"):
            entry["classifications"] = post.getlist(f"classifications_{index}")
    return entry


@login_required
def set_list(request):
    if request.method == "POST":
        clone_from = request.POST.get("clone_from") or ""
        if clone_from:
            source = set_for(request.user, clone_from)
            if source is None:
                messages.error(request, "Unknown set to clone.")
                return redirect("training:set_list")
            name = (request.POST.get("clone_name") or "").strip()
            if not name:
                name = f"Copy of {source.name}"
            training_set = clone_set(name, source, request.user)
            messages.success(
                request,
                f'Cloned "{source.name}" into "{name}" '
                f"with {training_set.items.count()} positions.",
            )
            return redirect("training:set_detail", pk=training_set.pk)

        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Give the set a name.")
            return redirect("training:set_list")
        # any number of entries, book and turning-point types mixed
        indexes = sorted({key.rsplit("_", 1)[1] for key in request.POST
                          if key.startswith("source_type_")})
        entries = []
        for index in indexes:
            entry = _entry_from_post(request.POST, index)
            if entry["source_type"] == "book" and not entry.get("book_slug"):
                continue  # empty entry row
            if entry["source_type"] == "turning_points" and not entry.get("player"):
                continue
            if entry["source_type"] == "rework" and not entry.get("from_set"):
                continue
            entries.append(entry)
        if not entries:
            messages.error(request, "Add at least one source.")
            return redirect("training:set_list")
        try:
            training_set = create_set(name, entries, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("training:set_list")
        if training_set.items.count() == 0:
            messages.warning(request, f'Set "{name}" has no positions for those ranges.')
        else:
            messages.success(
                request, f'Set "{name}" created with {training_set.items.count()} positions.'
            )
        return redirect("training:set_detail", pk=training_set.pk)

    sets = sets_for(request.user).annotate(
        total=Count("items"),
        pending=Count("items", filter=Q(items__status=TrainingSetItem.Status.PENDING)),
        passed=Count("items", filter=Q(items__status=TrainingSetItem.Status.PASSED)),
        rework=Count("items", filter=Q(items__status=TrainingSetItem.Status.REWORK)),
    ).order_by("-created_at")

    # 50 sets per page; the clone dropdown still lists every set.
    set_page = Paginator(sets, 50).get_page(request.GET.get("p"))

    context = {
        "sets": sets,
        "set_page": set_page,
        "books": Book.objects.order_by("title"),
        "players": Player.objects.order_by("username"),
        "classifications": TurningPoint.Classification.choices,
    }
    return render(request, "training/set_list.html", context)


@login_required
def rework_list(request):
    """All re-work items across sets, optionally scoped to one set or one book.

    `?set=<pk>` and `?book=<slug>` filter independently (both may apply).
    """
    items = (
        TrainingSetItem.objects.filter(
            status=TrainingSetItem.Status.REWORK,
            training_set__in=sets_for(request.user),
        )
        .select_related("training_set", "puzzle__book")
    )
    selected_set = None
    set_pk = request.GET.get("set") or ""
    if set_pk.isdigit():
        # another user's pk resolves to None: fall back to the unfiltered
        # (still scoped) list rather than leaking or 404-ing
        selected_set = set_for(request.user, set_pk)
        if selected_set is not None:
            items = items.filter(training_set=selected_set)
    selected_book = None
    book_slug = (request.GET.get("book") or "").strip()
    if book_slug:
        selected_book = Book.objects.filter(slug=book_slug).first()
        if selected_book is not None:
            items = items.filter(puzzle__book=selected_book)
    items = items.order_by("training_set__name", "order", "id")
    context = {
        "items": items,
        "sets": sets_for(request.user).annotate(
            rework=Count(
                "items", filter=Q(items__status=TrainingSetItem.Status.REWORK)
            )
        ).filter(rework__gt=0).order_by("name"),
        "books": Book.objects.filter(
            puzzles__training_items__status=TrainingSetItem.Status.REWORK,
            puzzles__training_items__training_set__in=sets_for(request.user),
        ).distinct().order_by("title"),
        "selected_set": selected_set,
        "selected_book": selected_book,
    }
    return render(request, "training/rework_list.html", context)


@login_required
def set_detail(request, pk):
    training_set = _set_or_404(request.user, pk)

    if request.method == "POST" and request.POST.get("add_positions"):
        indexes = sorted({key.rsplit("_", 1)[1] for key in request.POST
                          if key.startswith("source_type_")})
        entries = []
        for index in indexes:
            entry = _entry_from_post(request.POST, index)
            if entry["source_type"] == "book" and not entry.get("book_slug"):
                continue
            if entry["source_type"] == "turning_points" and not entry.get("player"):
                continue
            if entry["source_type"] == "rework" and not entry.get("from_set"):
                continue
            entries.append(entry)
        if not entries:
            messages.error(request, "Add at least one source.")
        else:
            try:
                added = add_entries(training_set, entries, request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Added {added} position(s) to the set.")
        return redirect("training:set_detail", pk=pk)

    category = request.GET.get("status", TrainingSetItem.Status.PENDING)
    if category not in {value for value, _ in CATEGORIES}:
        category = TrainingSetItem.Status.PENDING
    items = training_set.items.select_related("puzzle")
    context = {
        "training_set": training_set,
        "items": items.filter(status=category) if category != "all" else items,
        "category": category,
        "categories": CATEGORIES,
        "first_item": next_item(training_set, category),
        "sets": sets_for(request.user).exclude(pk=training_set.pk).annotate(
            rework=Count(
                "items",
                filter=Q(items__status=TrainingSetItem.Status.REWORK),
            )
        ).order_by("-created_at"),
        "books": Book.objects.order_by("title"),
        "players": Player.objects.order_by("username"),
        "classifications": TurningPoint.Classification.choices,
        "stats": _stats(training_set),
    }
    return render(request, "training/set_detail.html", context)


@login_required
def set_pdf(request, pk):
    """PDF of all the set's positions, in the app's default print layout."""
    training_set = _set_or_404(request.user, pk)
    pdf_bytes = render_set_pdf(training_set)
    response = FileResponse(io.BytesIO(pdf_bytes), content_type="application/pdf")
    name = slugify(training_set.name) or f"train-set-{training_set.pk}"
    response["Content-Disposition"] = f'attachment; filename="{name}_positions.pdf"'
    return response


@login_required
def item_session(request, pk, item_pk):
    training_set = _set_or_404(request.user, pk)
    item = get_object_or_404(
        TrainingSetItem.objects.select_related("puzzle"), pk=item_pk,
        training_set=training_set,
    )
    category = request.GET.get("status", "")
    if category not in {value for value, _ in CATEGORIES}:
        category = ""
    context = {
        "training_set": training_set,
        "item": item,
        "category": category,
        "next_item": next_item(training_set, category) if category else None,
        "stats": _stats(training_set),
    }
    return render(request, "training/item_session.html", context)


@login_required
def item_action(request, pk, item_pk):
    """JSON: fix the item's stored orientation (rotate 180°) or switch the
    side to move — both update the item's FEN snapshot in the DB and return
    the new FEN so the session board re-renders in place. `set_fen` replaces
    the position entirely (the position editor, for ill-scanned boards) and
    also updates the source puzzle when the item has one."""
    training_set = _set_or_404(request.user, pk)
    item = get_object_or_404(TrainingSetItem, pk=item_pk, training_set=training_set)
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "bad JSON"}, status=400)
    action = data.get("action")
    try:
        if action == "rotate":
            item.fen = rotate_position_180(item.fen)
        elif action == "toggle_turn":
            board = chess.Board(item.fen)
            board.turn = not board.turn
            item.fen = board.fen()
        elif action == "set_fen":
            fen = (data.get("fen") or "").strip()
            # structural validation only — kingless OCR positions are
            # allowed through so the user can repair them in the editor
            chess.Board(fen)
            item.fen = fen
            if item.puzzle is not None:
                puzzle = item.puzzle
                puzzle.fen = fen
                puzzle.piece_cells = []  # derived from the old FEN
                puzzle.save(update_fields=["fen", "piece_cells", "updated_at"])
        else:
            return JsonResponse({"error": "bad action"}, status=400)
    except ValueError:
        return JsonResponse({"error": "invalid FEN"}, status=400)
    item.save(update_fields=["fen"])
    return JsonResponse({"ok": True, "fen": item.fen})


@login_required
def mark(request, pk, item_pk):
    """JSON: mark one item passed / rework; returns the next in the category."""
    training_set = _set_or_404(request.user, pk)
    item = get_object_or_404(TrainingSetItem, pk=item_pk, training_set=training_set)
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "bad JSON"}, status=400)
    result = data.get("result")
    if result not in (TrainingSetItem.Status.PASSED, TrainingSetItem.Status.REWORK):
        return JsonResponse({"error": "bad result"}, status=400)
    # keep training inside the same category (pending by default)
    category = data.get("status", TrainingSetItem.Status.PENDING)
    if category not in {value for value, _ in CATEGORIES}:
        category = TrainingSetItem.Status.PENDING
    mark_item(item, result)
    nxt = next_item(training_set, category)
    query = f"?status={category}" if category != TrainingSetItem.Status.PENDING else ""
    return JsonResponse(
        {
            "ok": True,
            "item_status": item.status,
            "attempts": item.attempts,
            "next_item_url": (
                None if nxt is None else
                f"/train-sets/{training_set.pk}/items/{nxt.pk}/{query}"
            ),
            "stats": _stats(training_set),
        }
    )


@login_required
def mark_all_view(request, pk):
    training_set = _set_or_404(request.user, pk)
    if request.method != "POST":
        return redirect("training:set_detail", pk=pk)
    result = request.POST.get("result")
    status = request.POST.get("status", TrainingSetItem.Status.PENDING)
    if result in (TrainingSetItem.Status.PASSED, TrainingSetItem.Status.REWORK):
        count = mark_all(training_set, result, status=status)
        label = "passed" if result == TrainingSetItem.Status.PASSED else "re-work"
        messages.success(
            request, f"Marked {count} position(s) in the "
                     f"{dict(CATEGORIES).get(status, 'pending')} category as {label}."
        )
    return redirect("training:set_detail", pk=pk)
