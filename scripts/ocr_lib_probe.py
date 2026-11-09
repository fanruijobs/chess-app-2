"""Test the Chess_diagram_to_FEN library on a Polgar board crop.

Runs under the dedicated .venv313 (the library needs Python <= 3.13).
Usage:
  .venv313/bin/python scripts/test_ocr_lib.py <image>
"""
import importlib.util
import inspect
import sys
from pathlib import Path

REPO = Path("/tmp/chess_diagram_to_fen")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent))

# the repo has BOTH a package dir and a module file named the same — load
# the module file explicitly
spec = importlib.util.spec_from_file_location(
    "chess_diagram_to_fen", REPO / "chess_diagram_to_fen.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
get_fen = module.get_fen

print("signature:", inspect.signature(get_fen))

if len(sys.argv) > 1:
    from PIL import Image

    image = Image.open(sys.argv[1])
    print("image:", image.size)
    result = get_fen(
        img=image,
        game="chess",
        auto_rotate_image=True,
        auto_rotate_board=True,
    )
    print("RESULT_FEN:", result.fen)
