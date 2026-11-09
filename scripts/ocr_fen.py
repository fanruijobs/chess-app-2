"""Bridge: convert diagram images to FEN via tsoj/Chess_diagram_to_FEN.

Runs under .venv313 (the library needs Python <= 3.13). Two modes:
  .venv313/bin/python scripts/ocr_fen.py <image> [<image> ...]
      one "RESULT_FEN: <fen>" line per input, then exit.
  .venv313/bin/python scripts/ocr_fen.py --serve
      persistent mode: read batches (one line per batch, tab-separated image
      paths) from stdin; print one result line per image; models load ONCE.
"""
import os
import importlib.util
import sys
from pathlib import Path

REPO = Path(os.environ.get("BOOKS_OCR_REPO", "/tmp/chess_diagram_to_fen_v1"))
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "chess_diagram_to_fen", REPO / "chess_diagram_to_fen.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from PIL import Image  # noqa: E402


def to_fen(path: str) -> str:
    try:
        image = Image.open(path).convert("RGB")
        result = module.get_fen(
            img=image,
            game="chess",
            auto_rotate_image=True,
            auto_rotate_board=True,
        )
        return result.fen or ""
    except Exception as exc:  # noqa: BLE001 - one bad crop must not kill the batch
        return f"ERROR: {exc}"


if len(sys.argv) > 1 and sys.argv[1] == "--serve":
    for line in sys.stdin:
        paths = line.strip().split("\t")
        if not paths or not paths[0]:
            continue
        print("\t".join(to_fen(p) for p in paths), flush=True)
else:
    for path in sys.argv[1:]:
        fen = to_fen(path)
        if fen.startswith("ERROR:"):
            print(f"RESULT_ERROR: {path}: {fen}")
        else:
            print(f"RESULT_FEN: {fen}")
