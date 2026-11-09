"""Diagnostic: normalized 16x16 patches of occupied squares, as ASCII."""
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
from books.cv.squares import split_board, square_color

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
vx, hy = warped_grid_lines(d.board_img)
flat = [p for rank in split_board(d.board_img, vx=vx, hy=hy) for p in rank]
chars = " .:-=+*#%@"
shown = 0
for i, p in enumerate(flat):
    if extract_glyph(p) is None:
        continue
    r = cv2.resize(p, (16, 16), interpolation=cv2.INTER_AREA)
    print(f"square {i // 8 + 1}{chr(ord('a') + i % 8)} ({square_color(i)}):")
    for row in r:
        print("".join(chars[min(9, max(0, (255 - int(v)) * 10 // 256))] for v in row))
    shown += 1
    if shown >= 10:
        break
