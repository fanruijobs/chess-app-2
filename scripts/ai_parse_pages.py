#!/usr/bin/env python3
"""Parse chess-book pages into FENs with the DeepSeek vision API.

Evaluation-only: sends a full page image per request (one JSON response with
all diagrams on the page), validates the FENs, appends raw + validated
results to a JSONL cache in tmp/, and compares against the existing DB
puzzles. Never writes to the DB.

DeepSeek exposes an OpenAI-compatible API (base_url https://api.deepseek.com,
like analysis/llm.py) but `deepseek-chat` is text-only — the vision model is
`deepseek-v4-flash-vision-exp` (new/experimental, so run --smoke-test first).

Usage:
  .venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5
  .venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --smoke-test
  .venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5 --dry-run
  .venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5 --report-only
"""
import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

import chess  # noqa: E402
import cv2  # noqa: E402
import openai  # noqa: E402
from django.conf import settings  # noqa: E402

from books.cv.fen import validate_fen  # noqa: E402
from books.cv.io import render_pdf_page  # noqa: E402
from books.models import Book, Puzzle  # noqa: E402

DEFAULT_MODEL = "deepseek-v4-flash-vision-exp"
MAX_ATTEMPTS = 3  # API retry loop, mirrors analysis/llm.py

SYSTEM_PROMPT = (
    "You are a precise chess-diagram reader for a chess workbook. The page image "
    "contains a grid of solid chess diagrams (boards). Transcribe every board on "
    "the page to a FEN.\n"
    "\n"
    "Board style: squares are solid-filled (light squares near-white, dark squares "
    "gray); pieces are solid silhouettes (white pieces light, black pieces dark); "
    "no printed coordinates or numbers.\n"
    "\n"
    'For each board produce one object:\n'
    '  {"row": int, "col": int, "black_dot": bool, "fen": "..."}\n'
    "- row: 1 = top row ... 4 = bottom row of the grid.\n"
    "- col: 1 = leftmost ... 3 = rightmost column.\n"
    "- black_dot: true ONLY if a small filled black dot/circle mark is printed "
    "next to that diagram (Steps workbooks mark \"Black to move\" positions this "
    "way, usually in the white space between boards); otherwise false.\n"
    "- fen: full FEN. The second field (side to move) must be \"b\" when "
    "black_dot is true, otherwise \"w\". Use \"-\" for castling, en passant and "
    "the halfmove clock, and \"1\" for the fullmove number.\n"
    "\n"
    "FEN rules: 8 ranks from the board's top (rank 8) to bottom (rank 1), "
    'separated by "/". Uppercase letters for white pieces (K Q R B N P), '
    "lowercase for black (k q r b n p), digits for runs of empty squares. "
    "Exactly 8 ranks, exactly 8 files per rank, one king per side, max 32 pieces. "
    "Some slots may contain drawings or text instead of boards: skip them "
    "entirely. Do not invent boards.\n"
    "\n"
    'Reply with JSON only, no prose, no markdown fences:\n'
    '{"diagrams": [...]}'
)


class ConfigError(Exception):
    """DEEPSEEK_API_KEY is not set."""


def get_client() -> openai.OpenAI:
    key = settings.DEEPSEEK_API_KEY
    if not key:
        raise ConfigError(
            "DEEPSEEK_API_KEY is not set — add it to your environment "
            "(see .env.example) to run the AI page parser."
        )
    return openai.OpenAI(
        api_key=key, base_url="https://api.deepseek.com", timeout=300.0, max_retries=3
    )  # 300s: the vision model reasons for minutes on a full page


def resolve_pdf(book: Book, start: int, args) -> Path:
    """PDF path: --pdf flag, else PageImage.source_file, else uploaded-books/<slug>.pdf."""
    if args.pdf:
        path = Path(args.pdf)
    else:
        page = book.pages.filter(page_number=start).first()
        name = Path(page.source_file.split("#")[0]).name if page and page.source_file else f"{book.slug}.pdf"
        path = Path("uploaded-books") / name
        if not path.is_file() and page and page.source_file:
            # source_file may already point at a real location
            path = Path(page.source_file.split("#")[0])
    if not path.is_file():
        raise FileNotFoundError(f"cannot find book PDF: {path}")
    return path


def render_page(pdf: Path, page_number: int, dpi: int):
    """Render one page as a BGR ndarray. page_number is 1-based (PDF order)."""
    return render_pdf_page(pdf, page_number - 1, dpi=dpi)


def encode_image(img) -> str:
    """BGR ndarray -> base64 JPEG data URI (quality 90).

    JPEG, not PNG: the page renders are ~2.7 MB as PNG and the API
    intermittently reports those as "Image not accessible"; JPEG drops the
    payload ~6x with no visible loss on solid-fill diagram pages.
    """
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("failed to encode page image as JPEG")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def layout_text(profile) -> str:
    """Column/row fractions from the stored layout, for the prompt."""
    layout = profile.data.get("layout") or {}
    cols = layout.get("columns", [])
    rows = layout.get("rows", [])
    col_str = ", ".join(
        f"[{f0:.3f}, {f1:.3f}]" for f0, f1 in cols
    )
    row_str = ", ".join(
        f"[{f0:.3f}, {f1:.3f}]" for f0, f1 in rows
    )
    return (
        f"Grid: {len(cols)} columns x {len(rows)} rows.\n"
        f"Column x-fractions: [{col_str}]\n"
        f"Row y-fractions: [{row_str}]"
    )


def build_user_text(pdf: Path, page_number: int, img, profile) -> str:
    h, w = img.shape[:2]
    parts = [
        f"Page {page_number} of {pdf.name} ({w}x{h} px).",
        layout_text(profile),
        "Use these slot boxes to assign each diagram its row and column.",
        "Transcribe every board on this page.",
    ]
    return "\n".join(parts)


def build_messages(pdf, page_number, img_b64, profile, text: str) -> list[dict]:
    """system + one user message carrying the page image and the page text."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": img_b64}},
                {"type": "text", "text": text},
            ],
        },
    ]


CORRECTION_PREFIX = (
    "Your previous answer was invalid: {summary}\n"
    "Re-read those boards carefully, square by square, and return the corrected "
    "full JSON answer with ALL diagrams again, following the original rules.\n"
    "JSON only."
)


def call_model(client, model: str, messages: list[dict], max_tokens: int = 32768) -> str:
    """One chat.completions call with the llm.py retry loop.

    The vision model is a reasoning model: `max_tokens` covers reasoning +
    answer, so the budget must be generous (~32K) or the answer comes back
    empty. Retries RateLimitError/APIConnectionError (3 attempts, exponential
    backoff). If response_format is rejected (400), retries once without it.
    """
    response = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            break
        except (openai.RateLimitError, openai.APIConnectionError) as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            print(f"    attempt {attempt + 1} failed: {exc} — retrying in {2 ** attempt}s")
            time.sleep(2**attempt)
        except openai.BadRequestError as exc:
            if attempt == 0 and "response_format" in str(exc):
                print("    response_format rejected by the model — retrying without it")
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=max_tokens,
                )
                break
            raise
    choice = response.choices[0]
    content = choice.message.content or ""
    if not content and choice.finish_reason == "length":
        raise RuntimeError(
            "model hit the token limit before answering (reasoning consumed "
            "the max_tokens budget) — raise max_tokens"
        )
    return content


def parse_diagrams(content: str) -> list[dict]:
    """Extract the diagrams list from a JSON-ish model answer."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # last resort: take the outermost {...}
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object in model answer: {content[:200]!r}")
        data = json.loads(match.group(0))
    diagrams = data.get("diagrams")
    if not isinstance(diagrams, list):
        raise ValueError(f"model answer has no 'diagrams' list: {content[:200]!r}")
    return diagrams


def validate_diagram(d: dict, n_cols: int) -> dict | None:
    """One model diagram -> record dict, or None if it must be dropped."""
    try:
        row, col = int(d.get("row")), int(d.get("col"))
    except (TypeError, ValueError):
        return None  # not an addressable slot
    if not (1 <= row and 1 <= col and col <= n_cols):
        return None
    slot_index = (row - 1) * n_cols + col
    fen = (d.get("fen") or "").strip()
    if not fen:
        return {
            "row": row, "col": col, "slot_index": slot_index,
            "black_dot": bool(d.get("black_dot")), "fen": fen,
            "valid": False, "reasons": ["empty fen"],
        }
    fen, legal, reasons = validate_fen(fen)
    return {
        "row": row, "col": col, "slot_index": slot_index,
        "black_dot": bool(d.get("black_dot")), "fen": fen,
        "valid": legal, "reasons": reasons,
    }


def run_pages(client, model: str, args, book: Book, pdf: Path, profile):
    """Render + call the API for each page; return one cache record per page."""
    records = []
    for offset in range(args.count):
        page_number = args.start + offset
        print(f"page {page_number} ...")
        img = render_page(pdf, page_number, args.dpi)
        img_b64 = encode_image(img)
        text = build_user_text(pdf, page_number, img, profile)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "book": book.slug,
            "pdf": str(pdf),
            "page_number": page_number,
            "dpi": args.dpi,
            "image_size": [img.shape[1], img.shape[0]],
            "model": model,
            "attempts": 0,
            "retried": False,
            "error": None,
            "raw_content": None,
            "diagrams": [],
        }
        try:
            messages = build_messages(pdf, page_number, img_b64, profile, text)
            content = call_model(client, model, messages)
            record["attempts"] += 1
            record["raw_content"] = content
            try:
                raw_diagrams = parse_diagrams(content)
            except ValueError as exc:
                raise RuntimeError(f"unparseable answer: {exc}") from exc

            n_cols = len(profile.data["layout"]["columns"])
            diagrams, seen, problems = [], set(), []
            for d in raw_diagrams:
                vd = validate_diagram(d, n_cols)
                if vd is None:
                    problems.append("diagram outside the grid dropped")
                    continue
                if vd["slot_index"] in seen:
                    problems.append(f"duplicate slot {vd['slot_index']} dropped")
                    continue
                seen.add(vd["slot_index"])
                diagrams.append(vd)
                if not vd["valid"]:
                    problems.append(
                        f"slot {vd['slot_index']} invalid: {'; '.join(vd['reasons'])}"
                    )
            valid_fens = {d["fen"] for d in diagrams if d["valid"]}
            if len(valid_fens) <= 1 and len(diagrams) > 1:
                problems.append(
                    "all diagrams came back as the same position — the model "
                    "did not transcribe the boards"
                )
            record["diagrams"] = diagrams

            if not diagrams or problems:
                # one corrective retry with the full page image again
                record["retried"] = True
                summary = "; ".join(problems) or "no boards were returned"
                retry_messages = build_messages(
                    pdf, page_number, img_b64, profile,
                    text + "\n\n" + CORRECTION_PREFIX.format(summary=summary),
                )
                content = call_model(client, model, retry_messages)
                record["attempts"] += 1
                record["raw_content"] = content
                raw_diagrams = parse_diagrams(content)
                diagrams, seen = [], set()
                for d in raw_diagrams:
                    vd = validate_diagram(d, n_cols)
                    if vd is None or vd["slot_index"] in seen:
                        continue
                    seen.add(vd["slot_index"])
                    diagrams.append(vd)
                record["diagrams"] = diagrams
        except Exception as exc:  # noqa: BLE001 - record and continue
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {record['error']}")
        records.append(record)
    return records


def load_db_puzzles(book: Book, page_number: int) -> dict[int, Puzzle]:
    return {
        p.diagram_index: p
        for p in Puzzle.objects.filter(book=book, page__page_number=page_number)
    }


def diff_squares(board_a: chess.Board, board_b: chess.Board) -> int:
    return sum(
        1
        for sq in chess.SQUARES
        if board_a.piece_at(sq) != board_b.piece_at(sq)
    )


def compare_slot(ai: dict | None, db: Puzzle | None) -> dict:
    """Match level between one AI diagram and one DB puzzle (or None)."""
    if db is not None:
        try:
            db_board = chess.Board(db.fen)
            db_valid = True
            db_turn = "w" if db_board.turn else "b"
        except ValueError:
            db_board, db_valid, db_turn = None, False, None
    else:
        db_board = db_valid = db_turn = None

    if ai is None and db is None:
        return {"status": "-"}
    if ai is None:
        return {"status": "DB-MISSING", "number": db.number}
    if db is None:
        return {
            "status": "AI-MISSING" if ai["valid"] else "AI-INVALID",
            "fen": ai["fen"],
        }
    if not ai["valid"]:
        return {"status": "AI-INVALID", "number": db.number, "fen": ai["fen"]}
    if not db_valid:
        return {"status": "DB-INVALID", "number": db.number, "fen": ai["fen"]}

    ai_board = chess.Board(ai["fen"])
    ai_turn = "w" if ai_board.turn else "b"
    turn = "OK" if ai_turn == db_turn else "MISMATCH"
    if ai_board.fen() == db_board.fen():
        status = "FULL"
        diff = 0
    elif ai_board.board_fen() == db_board.board_fen():
        status = "PLACEMENT"
        diff = 0
    else:
        diff = diff_squares(ai_board, db_board)
        status = f"DIFF({diff})"
    return {"status": status, "diff": diff, "turn": turn, "number": db.number}


def print_report(records: list[dict], book: Book) -> None:
    """Latest record per page -> per-page comparison tables + grand totals."""
    latest: dict[int, dict] = {}
    for record in records:
        latest[record["page_number"]] = record

    totals = {}
    for page_number in sorted(latest):
        record = latest[page_number]
        db = load_db_puzzles(book, page_number)
        ai_by_slot = {d["slot_index"]: d for d in record["diagrams"]}
        invalid = sum(1 for d in record["diagrams"] if not d["valid"])
        print(
            f"\npage {page_number}  model={record['model']}  "
            f"ai={len(record['diagrams'])}  retry={'y' if record['retried'] else 'n'}"
            f"  error={record['error'] or '-'}"
        )
        counts = {}
        for slot in range(1, 13):
            row = compare_slot(ai_by_slot.get(slot), db.get(slot))
            status = row["status"]
            counts[status] = counts.get(status, 0) + 1
            turn = row.get("turn", "-")
            fen = ai_by_slot.get(slot, {}).get("fen", "")
            extra = f"  n={row['diff']}  turn={turn}" if "diff" in row else ""
            print(
                f"  {slot:>2} {row.get('number', '(none)'):<9} "
                f"{status:<11}{extra}  {fen[:24]}"
            )
        summary = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  summary: {summary}  invalid={invalid}")
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
    print("\ngrand totals: " + "  ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    print(
        "note: DB FENs come from the local CV/OCR pipeline (some are "
        "status=review) — disagreement is a review candidate, not proof of AI error."
    )


def load_cache(path: Path) -> list[dict]:
    records = []
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def smoke_test(client, model: str, args, book: Book, pdf: Path, profile) -> int:
    """One full-page call with the real prompt; verify a valid FEN comes back."""
    page_number = args.start
    print(f"smoke test: page {page_number} of {pdf.name} with model {model}")
    try:
        img = render_page(pdf, page_number, args.dpi)
        img_b64 = encode_image(img)
        text = build_user_text(pdf, page_number, img, profile)
        messages = build_messages(pdf, page_number, img_b64, profile, text)
        content = call_model(client, model, messages)
    except ConfigError as exc:
        print(f"FAILED: {exc}")
        return 1
    except openai.AuthenticationError as exc:
        print(f"FAILED: authentication error — bad DEEPSEEK_API_KEY? ({exc})")
        return 1
    except openai.NotFoundError as exc:
        print(
            f"FAILED: model not found ({exc}) — the vision model string "
            f"'{model}' may be wrong; try --model with the current DeepSeek model name."
        )
        return 1
    except openai.BadRequestError as exc:
        print(f"FAILED: bad request ({exc}) — image format or parameters unsupported.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    try:
        raw_diagrams = parse_diagrams(content)
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1
    n_cols = len(profile.data["layout"]["columns"])
    valid = 0
    for d in raw_diagrams:
        vd = validate_diagram(d, n_cols)
        if vd is None:
            print("  dropped out-of-grid diagram:", d)
            continue
        mark = "ok " if vd["valid"] else "BAD"
        print(
            f"  {mark} row={vd['row']} col={vd['col']} slot={vd['slot_index']} "
            f"dot={vd['black_dot']} {vd['fen']}"
        )
        valid += vd["valid"]
    if valid:
        print(f"smoke test passed: {valid} valid FEN(s)")
        return 0
    print("smoke test failed: no valid FENs in the answer")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--book", required=True, help="book slug")
    parser.add_argument("--start", required=True, type=int, help="first page (1-based PDF page number)")
    parser.add_argument("--count", type=int, default=1, help="number of pages")
    parser.add_argument("--pdf", help="path to the book PDF (default: resolved from DB/uploaded-books)")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", help=f"JSONL cache path (default: tmp/ai_parse_<slug>.jsonl)")
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent, no API")
    parser.add_argument("--report-only", action="store_true", help="print the comparison from cache, no API")
    parser.add_argument("--smoke-test", action="store_true", help="one page call; exit 0 only if a valid FEN comes back")
    args = parser.parse_args()

    try:
        book = Book.objects.get(slug=args.book)
    except Book.DoesNotExist:
        print(f"no book with slug {args.book!r}")
        return 1
    if not hasattr(book, "calibration") or not book.calibration.data.get("layout"):
        print(f"book {args.book!r} has no stored layout — run set_layout first")
        return 1
    profile = book.calibration
    pdf = resolve_pdf(book, args.start, args)

    repo_root = Path(__file__).resolve().parent.parent
    out_path = Path(args.out) if args.out else repo_root / "tmp" / f"ai_parse_{book.slug}.jsonl"

    if args.report_only:
        print_report(load_cache(out_path), book)
        return 0

    pages = [args.start + i for i in range(args.count)]
    print(
        f"book={book.slug} pdf={pdf} pages={pages[0]}..{pages[-1]} "
        f"dpi={args.dpi} model={args.model} out={out_path}"
    )

    if args.dry_run:
        for page_number in pages:
            img = render_page(pdf, page_number, args.dpi)
            b64_len = len(encode_image(img))
            print(
                f"  page {page_number}: pdf index {page_number - 1}, "
                f"{img.shape[1]}x{img.shape[0]} px, "
                f"base64 {b64_len / 1e6:.2f} MB"
            )
        return 0

    try:
        client = get_client()  # raises ConfigError -> fail fast
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.smoke_test:
        return smoke_test(client, args.model, args, book, pdf, profile)

    records = run_pages(client, args.model, args, book, pdf, profile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
    print(f"\ncache: {out_path}")
    print_report(load_cache(out_path), book)
    return 0


if __name__ == "__main__":
    sys.exit(main())
