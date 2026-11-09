"""Diagnostic: show glyph masks of a few squares of a real warped board."""
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
from books.cv.grid import detect_line_diagrams, warped_grid_lines
from books.cv.squares import split_board

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
vx, hy = warped_grid_lines(d.board_img)
flat = [p for rank in split_board(d.board_img, vx=vx, hy=hy) for p in rank]
for idx in (0, 1, 8, 9, 16, 24):
    patch = flat[idx]
    glyph = extract_glyph(patch)
    if glyph is None:
        print(f"square {idx}: EMPTY")
        continue
    small = cv2.resize(glyph.mask.astype(np.uint8) * 255, (16, 16),
                       interpolation=cv2.INTER_AREA)
    print(f"square {idx}: fg={glyph.fg_ratio:.3f} solidity={glyph.solidity:.2f}")
    for row in small:
        print("  " + "".join("#" if v > 60 else "." for v in row))
