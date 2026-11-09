"""Diagnostic: squares after grid-line erasure."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv import load_image
from books.cv.classify import extract_glyph
from books.cv.grid import detect_line_diagrams, erase_grid_lines, warped_grid_lines
from books.cv.squares import split_board

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
vx, hy = warped_grid_lines(d.board_img)
clean = erase_grid_lines(d.board_img, vx, hy)
flat = [p for rank in split_board(clean, vx=vx, hy=hy, baseline=True) for p in rank]
for idx in (0, 1, 3, 5):
    p = flat[idx]
    g = extract_glyph(p)
    print(f"square {idx}: mean={p.mean():.0f} min={p.min()} max={p.max()}",
          "EMPTY" if g is None else f"fg={g.fg_ratio:.3f}")
    if g:
        small = cv2.resize(g.mask.astype(np.uint8) * 255, (16, 16), interpolation=cv2.INTER_AREA)
        for row in small:
            print("".join("#" if v > 60 else "." for v in row))
