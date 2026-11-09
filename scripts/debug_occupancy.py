"""Diagnostic: the 6g square's mask (a piece square)."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv import load_image
from books.cv.classify import _ink_masks, extract_glyph
from books.cv.grid import detect_line_diagrams, warped_grid_lines
from books.cv.squares import split_board

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
vx, hy = warped_grid_lines(d.board_img)
flat = [p for rank in split_board(d.board_img, vx=vx, hy=hy) for p in rank]
patch = flat[5 * 8 + 6]  # g6
print("patch:", patch.shape, "min/max/mean:", patch.min(), patch.max(), patch.mean().round(1))
dark, light, dr, lr = _ink_masks(patch)
print(f"dark={dr:.3f} light={lr:.3f}")
mask = dark | light
count, labels = cv2.connectedComponents(mask.astype(np.uint8))
print("components:", count - 1)
areas = [(i, (labels == i).sum()) for i in range(1, count)]
areas.sort(key=lambda t: -t[1])
print("largest component areas:", areas[:4])
small = cv2.resize(mask.astype(np.uint8) * 255, (16, 16), interpolation=cv2.INTER_AREA)
for row in small:
    print("".join("#" if v > 60 else "." for v in row))
glyph = extract_glyph(patch)
print("glyph:", "EMPTY" if glyph is None else f"fg={glyph.fg_ratio:.3f}")
