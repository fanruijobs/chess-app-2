"""Diagnostic: ASCII-render one PDF page to verify its content."""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from books.cv.io import render_pdf_page

path = sys.argv[1] if len(sys.argv) > 1 else "uploaded-books/step-6-workbook-extra.pdf"
page_no = int(sys.argv[2]) - 1 if len(sys.argv) > 2 else 0
img = render_pdf_page(path, page_no, dpi=150)
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
small = cv2.resize(gray, (110, int(gray.shape[0] * 110 / gray.shape[1])),
                   interpolation=cv2.INTER_AREA)
chars = " .:-=+*#%@"
for row in small[::2]:
    print("".join(chars[min(9, (255 - int(v)) * 10 // 256)] for v in row))
