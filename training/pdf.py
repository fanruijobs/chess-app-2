"""Printable PDF export of a set's positions (WeasyPrint HTML -> PDF).

Uses the app's default print settings — the same layout as the book-page
PDF: A4, 2 columns x 3 rows of boards per page, page numbers in the footer.
Each cell shows the item's label (with a (B) mark when black is to move),
the board rendered from its FEN snapshot in the chess.com default style
(borderless), and the best move when known.
"""

import chess
import chess.svg
from django.template.loader import render_to_string

BOARD_SIZE = 320  # px (~62mm), same as the book-page PDF

# chess.com default board colors (same as the session board): ivory light
# squares, green dark squares. The coordinate margin is fully transparent so
# the board prints borderless, with the coordinates floating on the page.
BOARD_COLORS = {
    "square light": "#ebecd0",
    "square dark": "#739552",
    "margin": "#00000000",
    "coord": "#52514e",
}


def build_items(training_set) -> list[dict]:
    """One dict per printable item, in set order; unparseable FENs are
    skipped (ill-scanned strays have no board to show)."""
    items = []
    for item in training_set.items.order_by("order", "id"):
        try:
            board = chess.Board(item.fen)
        except ValueError:
            continue
        items.append(
            {
                "item": item,
                "side_mark": "(B)" if board.turn == chess.BLACK else "",
                "board_svg": chess.svg.board(
                    board, size=BOARD_SIZE, coordinates=True, colors=BOARD_COLORS
                ),
            }
        )
    return items


def render_set_pdf(training_set) -> bytes:
    """Render all positions of the set as PDF bytes."""
    items = build_items(training_set)
    # fixed 6 per page (2 columns x 3 rows), last page may be shorter
    chunks = [items[i:i + 6] for i in range(0, len(items), 6)] or [[]]
    pages = [
        {"count": len(chunk),
         "rows": [chunk[r:r + 2] for r in range(0, len(chunk), 2)]}
        for chunk in chunks
    ]
    html = render_to_string(
        "training/pdf/set_pdf.html",
        {"training_set": training_set, "pages": pages},
    )
    # Lazy import: WeasyPrint needs GTK/Pango at import time, so keep the web
    # app runnable on machines that only use non-PDF features.
    from weasyprint import HTML

    return HTML(string=html).write_pdf()
