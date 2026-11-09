"""Diagnostic: wide context around page 69 slot 1 to locate the printed number."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv.io import render_pdf_page
from books.cv.layout import layout_diagrams
from books.models import Book

book = Book.objects.get(slug="polgar-5334")
layout = book.calibration.data["layout"]
img = render_pdf_page("uploaded-books/Polgar-5334.pdf", 68, dpi=150)  # PDF page 69
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
slot = layout_diagrams(gray, layout)[0]
x, y, w, h = slot.bbox
print("slot bbox:", slot.bbox)
region = gray[max(0, y - 120):y + int(h * 0.5), x:x + int(w * 0.5)]
chars = " .:-=+*#%@"
small = cv2.resize(region, (110, 40), interpolation=cv2.INTER_AREA)
for i, row in enumerate(small):
    print("".join(chars[min(9, (255 - int(v)) * 10 // 256)] for v in row))
