"""OCR bridge: FEN extraction via tsoj/Chess_diagram_to_FEN (Python 3.13).

The main app runs on Python 3.14; the OCR library needs <= 3.13 (its
pyfastnoiselite dependency has no 3.14 wheels). The bridge shells out to the
dedicated .venv313 interpreter, which loads the library from the cloned repo.
Batch mode: all crops of a page are sent in one subprocess call so the torch
models load once per page instead of once per diagram.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import cv2

logger = logging.getLogger("books.cv")

OCR_REPO = os.environ.get(
    "BOOKS_OCR_REPO",
    "/tmp/chess_diagram_to_fen_v1",
)
OCR_PYTHON = os.environ.get(
    "BOOKS_OCR_PYTHON",
    str(Path(__file__).resolve().parent.parent / ".venv313/bin/python"),
)
OCR_SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts/ocr_fen.py")


def available() -> bool:
    return Path(OCR_PYTHON).exists() and Path(OCR_SCRIPT).exists()


class OcrSession:
    """One persistent OCR subprocess; the torch models load only once."""

    def __init__(self):
        self.process = None
        self._warmed = False

    def __enter__(self):
        if available():
            self.process = subprocess.Popen(
                [OCR_PYTHON, OCR_SCRIPT, "--serve"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, bufsize=1, cwd=OCR_REPO,
            )
            logger.info("OCR session started (subprocess pid %d)", self.process.pid)
        return self

    def __exit__(self, *exc_info):
        if self.process is not None:
            self.process.stdin.close()
            self.process.terminate()
            self.process.wait(timeout=10)

    def fens_from_crops(self, crops) -> list[str | None]:
        if self.process is None:
            return [None] * len(crops)
        if not crops:
            return []
        if not self._warmed:
            self._warmed = True
            logger.info("first OCR call in this session — torch models loading (~2 min)")
        paths = []
        for crop in crops:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                paths.append(tmp.name)
            cv2.imwrite(paths[-1], crop)
        try:
            self.process.stdin.write("\t".join(paths) + "\n")
            self.process.stdin.flush()
            line = self.process.stdout.readline().rstrip("\n")
            values = line.split("\t")
            return [v if v and not v.startswith("ERROR:") else None for v in values]
        finally:
            for path in paths:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def fens_from_crops(crops, timeout: int = 300) -> list[str | None]:
    """One-shot batch OCR (models load per call). Prefer OcrSession."""
    if not available():
        logger.warning("OCR bridge unavailable (%s)", OCR_PYTHON)
        return [None] * len(crops)
    if not crops:
        return []
    paths = []
    for crop in crops:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            paths.append(tmp.name)
        cv2.imwrite(paths[-1], crop)
    try:
        process = subprocess.run(
            [OCR_PYTHON, OCR_SCRIPT, *paths],
            capture_output=True, text=True, timeout=timeout,
            cwd=OCR_REPO,
        )
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    results = {}
    for line in process.stdout.splitlines():
        if line.startswith("RESULT_FEN:"):
            index = len(results)
            results[index] = line.split("RESULT_FEN:", 1)[1].strip()
    return [results.get(i) for i in range(len(crops))]


def crop_slot(img, bbox, margin: int = 100):
    """Crop a diagram slot with generous margin (the OCR needs room)."""
    x, y, w, h = bbox
    height, width = img.shape[:2]
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(width, x + w + margin)
    y1 = min(height, y + h + margin)
    return img[y0:y1, x0:x1]


# module-level default session, set by batch commands
_default_session: OcrSession | None = None


def set_session(session: OcrSession | None) -> None:
    global _default_session
    _default_session = session


def get_session() -> OcrSession | None:
    return _default_session


def batch_fens(crops) -> list[str | None]:
    """Batch OCR through the active session (or a one-shot call)."""
    session = get_session()
    if session is not None:
        return session.fens_from_crops(crops)
    return fens_from_crops(crops)
