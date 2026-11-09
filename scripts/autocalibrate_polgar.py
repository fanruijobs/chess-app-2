"""Auto-calibrate Polgar-5334 from page 16 diagram 1 + its known FEN.

The cluster example squares map to the known position, so labels are derived
instead of entered by hand.
"""
import os
import sys

import chess
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv.calibrate import build_profile_data, cluster_board
from books.cv.io import render_pdf_page
from books.cv.layout import layout_diagrams
from books.models import Book, CalibrationProfile

TRUTH_FEN = "3q1rk1/5pbp/5Qp1/8/8/2B5/5PPP/6K1 w - - 0 1"
PDF = "uploaded-books/Polgar-5334.pdf"
PAGE = 15  # 0-based -> PDF page 16

img = render_pdf_page(PDF, PAGE, dpi=150)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
book = Book.objects.get(slug="polgar-5334")
layout = book.calibration.data["layout"]
slot = layout_diagrams(gray, layout)[0]  # first slot: puzzle #1
print("slot bbox:", slot.bbox)

truth = chess.Board(TRUTH_FEN)
info = cluster_board(slot.board_img, cut=1.2, baseline=True)
labels = {}
unlabeled = []
for cluster_id, stats in sorted(info["clusters"].items()):
    square_name = stats["example_squares"][0] if stats["example_squares"] else None
    if square_name:
        piece = truth.piece_at(chess.parse_square(square_name))
        if piece is not None:
            code = ("w" if piece.color else "b") + piece.symbol().upper()
            labels[cluster_id] = code
            print(f"  {cluster_id}: {square_name} -> {code} (n={stats['count']})")
            continue
    unlabeled.append(cluster_id)
print("unlabeled clusters (no truth square):", unlabeled)

profile = book.calibration
profile.data = build_profile_data(
    info, labels_by_cluster=labels, empty_max_ink=0.04, baseline=True,
    source={"pdf": PDF, "page": PAGE + 1, "diagram": 1},
)
profile.data["layout"] = layout  # keep the stored layout
profile.save(update_fields=["data", "updated_at"])
book.calibration_status = Book.Calibration.READY
book.save(update_fields=["calibration_status", "updated_at"])
print("profile saved:", sorted({c["label"] for c in profile.data["clusters"].values()}))
