# Real page photos for CV calibration tests

Drop real photos/scans of puzzle-book pages here (e.g. `polgar_p12.jpg`).
This directory is gitignored — the photos stay local.

The smoke test `books/tests/test_real_pages_smoke.py` skips when this
directory is empty; with photos present it runs the extraction pipeline
over all of them and reports per-photo diagram counts + review flags —
the manual acceptance check for real-font robustness (synthetic tests
only prove the pipeline mechanics).
