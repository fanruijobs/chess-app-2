"""Diagnostic: where is the ink in the warped real board?"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv import load_image
from books.cv.grid import _ink_fraction_signal, detect_line_diagrams

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
board = d.board_img
blurred = cv2.GaussianBlur(board, (5, 5), 0)
binary = cv2.adaptiveThreshold(
    blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    cv2.THRESH_BINARY_INV, blockSize=51, C=10,
)
print("total ink fraction:", (binary > 0).mean().round(3))
row_sig = _ink_fraction_signal(binary, axis=0)
# compact profile: max over 8-row bands
for y0 in range(0, 640, 8):
    band = row_sig[y0:y0 + 8]
    print(f"{y0:3d}: {band.max():.2f}", end="  ")
    if (y0 // 8) % 8 == 7:
        print()
