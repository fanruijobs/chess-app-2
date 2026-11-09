"""Diagnostic: visualize the warped board of a detected real-page diagram."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv import load_image
from books.cv.grid import detect_line_diagrams

img = load_image("/tmp/polgar_p6.png")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
d = detect_line_diagrams(gray)[0]
board = d.board_img
small = cv2.resize(board, (64, 64), interpolation=cv2.INTER_AREA)
chars = " .:-=+*#%@"
for row in small[::2]:
    print("".join(chars[min(9, (255 - int(v)) * 10 // 256)] for v in row))
