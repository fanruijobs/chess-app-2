import io

import chess
import chess.svg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, IntegerField, Q
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string

from books.models import Book, PageImage, Puzzle


@login_required
def index(request):
    books = Book.objects.annotate(
        puzzle_count=Count("puzzles"),
        verified_count=Count("puzzles", filter=Q(puzzles__status=Puzzle.Status.VERIFIED)),
        review_count=Count(
            "puzzles",
            filter=Q(puzzles__status__in=[Puzzle.Status.EXTRACTED, Puzzle.Status.REVIEW]),
        ),
    )
    return render(request, "books/index.html", {"books": books})


@login_required
def book_detail(request, slug):
    book = get_object_or_404(Book, slug=slug)
    profile = getattr(book, "calibration", None)
    profile_data = (profile.data or {}) if profile else {}
    slots_numbering = bool(profile_data.get("numbering", {}).get("mode") == "slots")

    # direct navigation: ?goto=<puzzle number> jumps to that puzzle's page.
    # slots books accept loose forms ("7-3", "p7-3", "007-03") — normalized
    # to the book's own format before the exact match.
    goto = (request.GET.get("goto") or "").strip()
    if goto:
        if slots_numbering:
            raw = goto.lower()
            if raw.startswith("p"):
                raw = raw[1:]
            parts = [p for p in raw.replace("-", " ").split() if p]
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                number_format = profile_data["numbering"]["format"]
                goto = number_format.format(page=int(parts[0]), slot=int(parts[1]))
        target = book.puzzles.filter(number=goto).first()
        if target is not None:
            return redirect("books:puzzle_view", pk=target.pk)
        messages.warning(request, f"No puzzle #{goto} in this book.")

    pages = book.pages.all()
    puzzles = book.puzzles
    if slots_numbering:
        # steps books: p03-01 style numbers are positional — order by page/slot
        puzzles = puzzles.order_by("page__page_number", "diagram_index", "id")
    else:
        puzzles = puzzles.annotate(
            number_int=Cast("number", output_field=IntegerField()),
        ).order_by("number_int", "id")

    # per-book-page filter (?page=N): all puzzles of that scanned page
    page_filter = request.GET.get("page", "")
    selected_page = None
    if page_filter.isdigit():
        selected_page = pages.filter(page_number=int(page_filter)).first()
        if selected_page:
            puzzles = puzzles.filter(page=selected_page)

    stats = book.puzzles.aggregate(
        total=Count("id"),
        verified=Count("id", filter=Q(status=Puzzle.Status.VERIFIED)),
        review=Count(
            "id", filter=Q(status__in=[Puzzle.Status.EXTRACTED, Puzzle.Status.REVIEW])
        ),
        solved=Count("id", filter=Q(solved__gt=0)),
    )

    # pagination: 30 puzzles per page
    from django.core.paginator import Paginator

    paginator = Paginator(puzzles, 30)
    try:
        page_number = max(1, int(request.GET.get("p", 1)))
    except ValueError:
        page_number = 1
    puzzle_page = paginator.get_page(page_number)

    context = {
        "book": book,
        "pages": pages,
        "puzzles": puzzle_page,
        "page_filter": int(page_filter) if page_filter.isdigit() else "",
        "selected_page": selected_page,
        "slots_numbering": slots_numbering,
        "stats": stats,
    }
    return render(request, "books/book_detail.html", context)


# --- review -----------------------------------------------------------------


@login_required
def review_queue(request):
    """Puzzles needing review, lowest confidence first."""
    puzzles = (
        Puzzle.objects.filter(status__in=[Puzzle.Status.EXTRACTED, Puzzle.Status.REVIEW])
        .select_related("book", "page")
        .order_by("confidence", "book__title", "number", "id")
    )
    book_filter = request.GET.get("book", "")
    if book_filter:
        puzzles = puzzles.filter(book__slug=book_filter)
    counts = (
        Puzzle.objects.filter(
            status__in=[Puzzle.Status.EXTRACTED, Puzzle.Status.REVIEW]
        )
        .values("book__slug", "book__title")
        .annotate(n=Count("id"))
        .order_by("book__title")
    )
    context = {
        "puzzles": puzzles,
        "counts": counts,
        "book_filter": book_filter,
    }
    return render(request, "books/review_queue.html", context)


@login_required
def bad_fens(request):
    """Puzzles whose stored FEN is illegal (malformed, missing kings, more
    than 32 pieces) — each row links to the review editor for manual repair."""
    from books.cv.fen import validate_fen

    bad = []
    for puzzle in (
        Puzzle.objects.select_related("book", "page")
        .order_by("book__title", "number")
    ):
        fen, legal, reasons = validate_fen(puzzle.fen)
        if not legal:
            bad.append((puzzle, fen, reasons))
    book_filter = request.GET.get("book", "")
    if book_filter:
        bad = [row for row in bad if row[0].book.slug == book_filter]
    context = {
        "bad": bad,
        "book_filter": book_filter,
        "books": Book.objects.order_by("title"),
    }
    return render(request, "books/bad_fens.html", context)


@login_required
def review_puzzle(request, pk):
    puzzle = get_object_or_404(
        Puzzle.objects.select_related("book", "page"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "verify":
            fen = (request.POST.get("fen") or "").strip()
            number = (request.POST.get("number") or "").strip()
            valid, reasons = _validate_fen(fen)
            if not valid:
                messages.error(request, "Invalid position: " + "; ".join(reasons))
            else:
                puzzle.fen = fen
                puzzle.number = number
                puzzle.status = Puzzle.Status.VERIFIED
                puzzle.confidence = 1.0
                puzzle.save(update_fields=["fen", "number", "status", "confidence", "updated_at"])
                messages.success(request, "Puzzle verified.")
                return redirect("books:review_queue")
        elif action == "save":
            puzzle.fen = (request.POST.get("fen") or "").strip() or puzzle.fen
            puzzle.number = (request.POST.get("number") or "").strip()
            puzzle.save(update_fields=["fen", "number", "updated_at"])
            messages.info(request, "Saved (still unverified).")
            return redirect("books:review_puzzle", pk=pk)
        elif action == "reject":
            puzzle.status = Puzzle.Status.REJECTED
            puzzle.save(update_fields=["status", "updated_at"])
            messages.info(request, "Puzzle rejected.")
            return redirect("books:review_queue")
        elif action == "rerun":
            return _rerun_extraction(request, puzzle)

    context = {
        "puzzle": puzzle,
        "page": puzzle.page,
        "has_crop": bool(puzzle.page and puzzle.page.image and puzzle.diagram_bbox),
    }
    return render(request, "books/review_puzzle.html", context)


def _validate_fen(fen: str) -> tuple[bool, list[str]]:
    reasons = []
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return False, [f"malformed FEN: {exc}"]
    if not board.is_valid():
        reasons.append("position invalid (kings/pawns)")
    return not reasons, reasons


def _rerun_extraction(request, puzzle):
    from books.services import get_profile
    from books.cv import CalibrationError, extract_page, load_image

    if not (puzzle.page and puzzle.page.image):
        messages.error(request, "This puzzle has no source page to re-extract.")
        return redirect("books:review_puzzle", pk=puzzle.pk)
    try:
        profile = get_profile(puzzle.book)
        img = load_image(puzzle.page.image.path)
        results = extract_page(
            img, profile,
            page_number=puzzle.page.page_number,
        )
    except CalibrationError as exc:
        messages.error(request, str(exc))
        return redirect("books:review_puzzle", pk=puzzle.pk)
    if not results:
        messages.error(request, "No diagrams found on re-extraction.")
        return redirect("books:review_puzzle", pk=puzzle.pk)
    if puzzle.diagram_index and getattr(results[0], "slot_index", None):
        # steps layouts: match the grid position, not the list index
        result = next(
            (r for r in results if r.slot_index == puzzle.diagram_index), None
        )
        if result is None:
            messages.error(request, "This slot was not extracted on re-run.")
            return redirect("books:review_puzzle", pk=puzzle.pk)
    else:
        index = min(puzzle.diagram_index or 1, len(results)) - 1
        result = results[index]
    puzzle.fen = result.fen
    puzzle.confidence = result.confidence
    puzzle.diagram_bbox = result.bbox
    puzzle.piece_cells = result.cells
    puzzle.extraction_notes = "; ".join(result.notes)
    if puzzle.status != Puzzle.Status.VERIFIED:
        puzzle.status = (
            Puzzle.Status.REVIEW if (result.confidence < 0.8 or not result.legal)
            else Puzzle.Status.EXTRACTED
        )
    puzzle.save(update_fields=["fen", "confidence", "diagram_bbox", "piece_cells",
                               "extraction_notes", "status", "updated_at"])
    messages.info(request, f"Re-extracted (confidence {result.confidence}).")
    return redirect("books:review_puzzle", pk=puzzle.pk)


@login_required
def diagram_crop(request, page_id, index):
    """The source diagram cropped from the page photo (PNG)."""
    page = get_object_or_404(PageImage, pk=page_id)
    puzzle = (
        page.puzzles.filter(diagram_index=index).first()
        if index else page.puzzles.first()
    )
    bbox = puzzle.diagram_bbox if puzzle else None
    if bbox is None:
        raise Http404("no diagram bbox for this page")
    from PIL import Image as PILImage

    with PILImage.open(page.image.path) as img:
        x, y, w, h = [int(v) for v in bbox]
        # generous margins: the stored slot bbox is tight around the board
        # and pieces at the edges get clipped otherwise
        x0 = max(0, x - int(w * 0.15))
        y0 = max(0, y - int(h * 0.15))
        x1 = min(img.width, x + w + int(w * 0.15))
        y1 = min(img.height, y + h + int(h * 0.15))
        crop = img.crop((x0, y0, x1, y1)).convert("RGB")
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
    return HttpResponse(buf.getvalue(), content_type="image/png")


@login_required
def puzzle_board_svg(request, pk):
    """Server-rendered board for a puzzle's FEN."""
    puzzle = get_object_or_404(Puzzle, pk=pk)
    board = chess.Board(puzzle.fen)
    svg = chess.svg.board(board, size=560, coordinates=True)
    return HttpResponse(svg, content_type="image/svg+xml")


@login_required
def book_page_pdf(request, slug, page_number):
    """PDF of all puzzles on one scanned book page — exactly 6 per PDF page
    (2x3 fixed grid), no FENs, black-to-move marked as #p018-01(B)."""
    book = get_object_or_404(Book, slug=slug)
    page = get_object_or_404(PageImage, book=book, page_number=page_number)
    puzzles = list(page.puzzles.order_by("number", "id"))
    items = []
    for puzzle in puzzles:
        try:
            board = chess.Board(puzzle.fen)
        except ValueError:
            continue  # unparseable strays have no board to show
        side_mark = "(B)" if puzzle.fen.split(" ")[1] == "b" else ""
        items.append(
            {
                "puzzle": puzzle,
                "side_mark": side_mark,
                "board_svg": chess.svg.board(board, size=320, coordinates=True),
            }
        )
    # fixed 6 per page (2 columns x 3 rows), last page may be shorter
    chunks = [items[i:i + 6] for i in range(0, len(items), 6)] or [[]]
    pages = [
        {"count": len(chunk),
         "rows": [chunk[r:r + 2] for r in range(0, len(chunk), 2)]}
        for chunk in chunks
    ]
    html = render_to_string(
        "books/pdf/book_page_pdf.html",
        {"book": book, "page": page, "pages": pages},
    )
    from weasyprint import HTML as WeasyHTML

    pdf_bytes = WeasyHTML(string=html).write_pdf()
    response = FileResponse(io.BytesIO(pdf_bytes), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{book.slug}_page_{page_number}.pdf"'
    )
    return response


def rotate_position_180(fen: str) -> str:
    """Rotate a position 180° in notation (a1 <-> h8), colors unchanged.

    For scans the OCR read from the wrong side: the stored FEN has black at
    the bottom while the scan shows white at the bottom. The tsoj pipeline
    rotates diagrams it judges to be from black's perspective; when that
    judgement is wrong, rotating the stored FEN 180° restores the truth.
    The side to move is preserved. Clicking twice is a no-op.
    """
    board = chess.Board(fen)
    rotated = board.transform(chess.flip_vertical).transform(chess.flip_horizontal)
    # in the rotated position the king sits off e1/e8, so castling rights no
    # longer apply (and fen() would sanitize them away anyway)
    rotated.castling_rights = 0
    return rotated.fen()


@login_required
def puzzle_view(request, pk):
    """A single puzzle rendered as a server-side SVG board.

    No client JavaScript: the board is inline SVG (works everywhere), and
    the flip/turn switches are plain links that re-render the page.
    """
    puzzle = get_object_or_404(Puzzle.objects.select_related("book", "page"), pk=pk)

    if request.method == "POST":
        if not request.user.is_superuser:
            messages.error(
                request, "Only an administrator can change the stored position."
            )
            return redirect("books:puzzle_view", pk=pk)
        action = request.POST.get("action")
        if action == "rotate":
            try:
                puzzle.fen = rotate_position_180(puzzle.fen)
            except ValueError:
                messages.error(request, "Cannot rotate: the stored FEN is invalid.")
            else:
                if puzzle.piece_cells:
                    # cells are stored rank 8 first; a 180° rotation reverses them
                    puzzle.piece_cells = list(reversed(puzzle.piece_cells))
                puzzle.save(update_fields=["fen", "piece_cells", "updated_at"])
                messages.success(
                    request,
                    "Position rotated 180°: the FEN now matches the scan's "
                    "white-at-bottom orientation. Saved.",
                )
        elif action == "toggle_turn":
            board = puzzle.board()
            if board is None:
                messages.error(
                    request, "Cannot switch side to move: the stored FEN is invalid."
                )
            else:
                board.turn = not board.turn
                puzzle.fen = board.fen()
                puzzle.save(update_fields=["fen", "updated_at"])
                label = "white" if board.turn == chess.WHITE else "black"
                messages.success(
                    request, f"Side to move switched to {label} and saved."
                )
        return redirect("books:puzzle_view", pk=pk)

    orientation = chess.BLACK if request.GET.get("flip") == "1" else chess.WHITE
    turn = request.GET.get("turn", "")
    fen = puzzle.fen
    if turn in ("w", "b"):
        fen = fen.replace(" w ", f" {turn} ", 1).replace(" b ", f" {turn} ", 1)
    board = chess.Board(fen)
    svg = chess.svg.board(board, size=480, coordinates=True, orientation=orientation)

    # previous / next puzzle of the same book, in the book's own ordering
    # (numeric number for printed books, page/slot for steps books)
    profile = getattr(puzzle.book, "calibration", None)
    profile_data = (profile.data or {}) if profile else {}
    slots_numbering = bool(profile_data.get("numbering", {}).get("mode") == "slots")
    prev_puzzle = next_puzzle = None
    book_puzzles = puzzle.book.puzzles
    if slots_numbering and puzzle.page is not None:
        page_number = puzzle.page.page_number
        prev_puzzle = book_puzzles.filter(
            Q(page__page_number__lt=page_number)
            | Q(page__page_number=page_number,
                diagram_index__lt=puzzle.diagram_index)
        ).order_by("-page__page_number", "-diagram_index", "-id").first()
        next_puzzle = book_puzzles.filter(
            Q(page__page_number__gt=page_number)
            | Q(page__page_number=page_number,
                diagram_index__gt=puzzle.diagram_index)
        ).order_by("page__page_number", "diagram_index", "id").first()
    elif puzzle.number and puzzle.number.isdigit():
        annotated = book_puzzles.annotate(
            number_int=Cast("number", output_field=IntegerField())
        )
        number = int(puzzle.number)
        prev_puzzle = annotated.filter(number_int__lt=number).order_by(
            "-number_int", "-id"
        ).first()
        next_puzzle = annotated.filter(number_int__gt=number).order_by(
            "number_int", "id"
        ).first()

    context = {
        "puzzle": puzzle,
        "page": puzzle.page,
        "board_svg": svg,
        "display_fen": fen,
        "flipped": orientation == chess.BLACK,
        "turn": " w " if " w " in fen else " b ",
        "prev_puzzle": prev_puzzle,
        "next_puzzle": next_puzzle,
    }
    return render(request, "books/puzzle_view.html", context)
