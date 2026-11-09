# PROJECT_CONTEXT — Chess Trainer

Generated 2026-08-20. What this repo is, how it works, and its current state.

## 1. What this project is

A personal, single-user Django web app with two halves:

1. **Game analysis** — download chess.com games, analyze them with Stockfish,
   find *turning points* (evaluation drops), classify mistakes (rules in
   `analysis/classifiers.py`), optionally get LLM explanations (DeepSeek API),
   and export printable PDFs including a *masked training mode* (positions
   only, answers at the end).
2. **Puzzle books** — scan/OCR chess books into a puzzle database, review and
   verify the extractions, generate engine solutions, and train on them.

All pages require login at `/accounts/login/` (no signup; users are created in
the Django admin). Training sets are per-user — each has one owner and is
visible only to them; superusers see all. The frontend is deliberately
**no-JS**: server-rendered templates, inline SVG boards, plain POST forms.

## 2. Stack / environment

- Python **3.14** venv (`.venv`), SQLite dev DB (`db.sqlite3`)
- Django, python-chess (PyPI `chess`, v1.11.2 — see gotchas §7), OpenCV, NumPy
- WeasyPrint (PDF export; needs `brew install pango`), Stockfish (brew)
- **OCR bridge**: [tsoj/Chess_diagram_to_FEN](https://github.com/tsoj/Chess_diagram_to_FEN)
  runs under a dedicated **`.venv313`** (its `pyfastnoiselite` dep has no 3.14
  wheels). The repo must be cloned at **tag 1.0** into
  `/tmp/chess_diagram_to_fen_v1` (upstream's current master restructured the
  package and no longer ships the single module the bridge loads;
  `BOOKS_OCR_REPO` overrides the path). Models from the 1.0 GitHub release
  (~864 MB). See `books/ocr.py` + `scripts/ocr_fen.py`.

## 3. Apps

| App | Purpose | Key files |
|---|---|---|
| `config` | Settings, root URLs | `settings.py` (`BOOKS_REVIEW_THRESHOLD=0.80`, `BOOKS_SOLUTION_*`) |
| `chessdata` | chess.com data: `Player`, `Game` (PK = chess.com uuid), `MoveEval` | `models.py`, command `fetch_games` |
| `analysis` | `AnalysisJob`, `TurningPoint` + classifiers, per-time-class profiles, LLM, PDF export | `classifiers.py`, `turning_points.py`, `profiles.py`, `llm.py`, `pdf.py`; commands `queue_games`, `analyze_worker`, `enrich_turning_points`, `export_pdf`, `analyze_games` |
| `books` | `Book`, `PageImage`, `CalibrationProfile`, `Puzzle`; CV pipeline + OCR bridge (steps scanner in `books/cv/steps.py`) | `books/cv/`, `books/ocr.py`, `services.py`; commands: `calibrate_book`, `set_layout` (incl. `--steps`), `import_pages`, `scan_page`, `verify_numbers`, `export_puzzles` |
| `training` | `TrainingSet`, `TrainingSetItem`, `TrainingAttempt` — user-marked training; engine lines computed on the fly (MultiPV 3) | `services.py` (create_set/mark_item/analyze_position), views (session, analyze/mark JSON, rework_list) |

## 4. Books app — data model notes

- `Puzzle.number` is a **CharField** (book's own numbering, verbatim). All
  numeric operations must cast: `Cast("number", output_field=IntegerField())`.
  Unique per book when non-empty; also unique `(book, page, diagram_index)`.
- `PageImage` unique `(book, page_number)`. `store_page_image` **upserts** by
  page_number (then source_file) — re-importing a page updates in place.
- `Puzzle.piece_cells`: JSON, 64 entries **rank-8-first** (h8 … a1), cluster
  ids or None. The OCR path leaves it `[]`.
- `CalibrationProfile.data` JSON: `clusters`, `templates`, `empty`,
  `solidity_cut`, `features`, `source` (CV), plus `layout` (diagram slot grid
  as page fractions), `first_page` (first puzzle page, numbering base),
  `ignore_pages` (OCR path).
- Puzzle statuses: `extracted` → `review` (low confidence / illegal) →
  `verified` / `rejected`. Verified puzzles are never overwritten by imports
  unless `--force`; upserts preserve puzzle IDs.

## 5. Key workflows

### Book import (OCR path — used for Polgar 5334)
1. `set_layout <slug> <pdf> <page>` — store the fixed diagram grid as page
   fractions (works for every page, no per-page detection variance).
2. `import_pages <slug> <pdf> --page-number-start N [--limit M] [--skip-existing] [--log-file tmp/import.log]`
   — each slot is cropped +100px margin and OCR'd by the .venv313 subprocess.
   Numbering: the OCR-detected **printed** number is authoritative, else
   `number_start + slot`. ~2min session warmup (torch models), ~9s/page.
   Bad pages log `FAILED` and no longer kill the batch.
3. Review queue (`/review/`) → verify/fix FEN + number per puzzle.
4. `/train-sets/` — training sets that **mix any number of sources**: book
   ranges (page and/or number), turning points (player + date range), and
   the Re-work category of other sets; user-marked **pass / re-work** with
   category tabs (Pending / Re-work / Passed) as the results page; sets can
   be extended with more positions after creation or cloned (fresh copies,
   all pending); second-attempt twins are named `<name> att. 2`. The
   session board runs
   **Stockfish in the browser** — `stockfish.js` (npm 10.0.2, asm.js worker
   build) **loaded from jsDelivr CDN** with the local copy
   (`books/static/books/vendor/stockfish.js`) as offline fallback, through a
   same-origin glue blob worker (`analysis/static/analysis/engine.js`;
   browsers forbid cross-origin workers): `go depth 25 movetime 1000`,
   MultiPV 3, auto-refreshed per move, toggled by the **Analysis button**
   (blue when off; green "Hide Analysis" while the lines show) — **no
   backend analysis traffic** (the old `/analyze/` and `/legal/` endpoints
   were removed). **chess.js** enforces legal moves. **Open in Chess.com**
   opens the current position (FEN in the URL) on chess.com's analysis
   board. Flip / fix-orientation / switch-side-to-move buttons save to the
   item's FEN snapshot (the only server round-trips on that page, alongside
   marking). The standalone
   **Analysis board** (`/analysis/`, nav-level) is pure frontend: from-start
   or custom-position (click-to-place editor) modes, zero server traffic,
   shared engine module in `analysis/static/analysis/engine.js`.

### Book import — Steps-workbook books (solid-fill 3x4 grids)

`set_layout --steps <slug> <pdf> <page> [--first-page N]` calibrates the
12-slot grid; numbering is positional `p<page:03d>-<slot:02d>` (right-then-
down, drawing slots skipped, never renumbered). The steps pipeline
(`books/cv/steps.py`): per-page band detection (stored-layout fallback when
drawings fragment the bands, dpi-independent short-band expansion), a board
gate combining **checkerboard correlation (absolute — phase-shifted boards
correlate negatively) OR white gap-line counts**, shifted-crop retry, and a
**region-OCR fallback** that lets tsoj localize the board inside the whole
band region (recovers boards that sit off the detected grid). A book whose
scan lost the square structure entirely (vignette + tilt + noise) is NOT
recoverable — re-scan instead.

### Book page PDF

`/books/<slug>/pages/<n>/pdf/` exports **exactly 6 puzzles per PDF page** in
a fixed 2×3 grid: compact designated title zone, identical board sizes
across pages, no FENs, black-to-move numbered `#p007-01(B)`; unparseable
strays are skipped.

### Puzzle page (`/puzzles/<pk>/`)
- **Flip board** — view-only display orientation.
- **White/Black to move ⇄ switch (saves)** — POST `toggle_turn`, flips the
  turn in the stored FEN, persisted.
- **Fix orientation: rotate notation 180° (saves)** — POST `rotate`, for scans
  where the OCR's orientation model misjudged the diagram as black's
  perspective: the stored FEN is the truth rotated 180° (colors preserved);
  this rotates it back (`rotate_position_180` in `books/views.py`). Also
  rotates `piece_cells` (reversed); drops castling
  rights (the rotated king is off e1/e8). Self-inverse.

### Book detail (`/books/<slug>/`)
- Pagination ordered **numerically** by puzzle number (cast to int),
  30/page (`?p=N`); `?page=N` filters to one scanned page.
- Direct navigation boxes: `?goto=<number>` → redirects to the puzzle; `p`
  box jumps pagination pages.

### Bad FENs (`/bad-fens/`)
Lists every puzzle whose stored FEN fails `books/cv/fen.validate_fen`
(malformed, missing/extra kings, >32 pieces) with a per-book filter; each
row links into `/review/<pk>/` (the click-to-fix board editor) for manual
repair.

### Re-work page (`/rework/`)
All Re-work training items across sets with `?set=` / `?book=` filters;
rows open the training session with `?status=rework` (next/prev stays in
the category).

### AI page parse (DeepSeek vision, evaluation-only)
`scripts/ai_parse_pages.py` — a whole page image → `deepseek-v4-flash-vision-exp`
→ all diagrams' FENs in one JSON answer; layout fractions from the profile
anchor row/col to slot numbers. Never writes the DB: JSONL cache in `tmp/`,
comparison vs stored puzzles (`--report-only` re-prints). Reasoning-model
gotchas: ~32K `max_tokens` (reasoning + answer share it), JPEG payloads
(large PNGs intermittently "not accessible"), degenerate "all diagrams
identical" answers trigger one corrective retry.

### Game analysis
`fetch_games <user>` → `queue_games` → `analyze_worker` (daemon) →
`enrich_turning_points` (LLM explanations) → `export_pdf` (masked mode
supported).

## 6. Current state — Polgar 5334 import (2026-08-20)

1184-page PDF, 6 puzzles/page, 2×3 slots, first puzzle page 16. Book's last
diagram: page 970 = #5334. Verified good through #1224 (page 221) on 08-19;
full import ran through page 1184 overnight.

- 5151 puzzles; **36 numbered > 5334** (runaway numbering, delete first);
  **221 numbers missing** in 1–5334.
- Missing blocks: **4463–4506** (misnumbered onto pages 821–828), **4508–4648**
  (pages 829–851 were absent; re-imported 08-20, created 138), **4748–4800**,
  **4802–4828** (pages 869–874 + breaks).
- Pages with no import: 67, 869–874, … (40 total). Numbering breaks at pages
  767, 807, 816, 821, 852, 882. Page 828 re-import collides on the unique
  `(book, number)` constraint (detected 4505+ already exists) — its old rows
  must be deleted first.
- **Black to move**: 762 puzzles flipped to black to move (2026-08-24) from a
  user-curated number list (ranges 1255–1470, 4139–4462, 5279–5333 plus
  singles; all 762 were present in the DB). The OCR import normalizes
  diagrams to White's perspective, so the original side-to-move is always
  recovered manually this way.
- `sketch.txt` = the cleanup command scratchpad (delete >5334, list missing,
  find absent pages / numbering breaks, re-import batches, server start/stop).
- **Steps series**: `step-6-extra` (Steps 6 Workbook Extra) is fully imported
  (~1300 puzzles, `p003-01`…`p113-12`); the steps scanner is stable (see §5).
  The plain `step-6` PDF was a bad scan (vignette, tilted boards, lost
  square structure) — all three detectors fail on it; **dropped, re-scan
  needed**. 279 of its puzzles are marked **black to move** (user-curated
  per-page slot lists, flipped 2026-08-24).
- **Horsehead** (`horsehead` slug, imported 2026-08-20): 176-page trilingual
  tactics book, unified numbering 1–1182. **1014 puzzles** imported with
  printed numbers authoritative (numbering mode `"printed"` — number OCR'd
  above each board's top-left corner with book-specific digit templates
  bootstrapped from macOS-Vision labels; unreadable bands stay unnumbered,
  never positionally guessed). The 5x5 (d4–h8) sub-board chapter pages are
  skipped by the board gate (dark-checkerboard requirement in
  `is_full_board`): 168 numbers (14 blocks) intentionally absent. 2 puzzles
  in review. Region-OCR fallback now requires ≥1 gated slot per page;
  `ocr_only_pages` profile entries handle piece-heavy pages (41, 128).
  Reconciliation scripts in `tmp/` (`reconcile_hh.py`, `fill_hh_numbers.py`,
  Vision ground truth `hh_ocr.jsonl`).
- The generic CV path (`books/cv/`) remains the fallback for other books; it
  works on synthetic boards but struggles with thin/dotted line-drawn pieces.
- **Bad FENs** (2026-08-25 scan, `/bad-fens/`): 19 illegal FENs total —
  `polgar-5334` 8 (all OPPOSITE_CHECK — the side not to move is in check,
  so placement or turn is wrong), `horsehead` 2 (OPPOSITE_CHECK),
  `step-6-extra` 9 (3 OPPOSITE_CHECK, 1 no black king, 1 two black kings,
  1 with 38 pieces, 3 missing kings — five of the nine sit on page 88, the
  AI-parser test page).
- **AI page parser** (`scripts/ai_parse_pages.py`, 2026-08-24): DeepSeek
  vision evaluation harness, smoke-tested on `step-6-extra` page 88 (12/12
  valid FENs after fixing the reasoning-budget and JPEG issues); the
  5-page batch (88–92) has not been completed yet.

## 7. Gotchas that have bitten this project

- **python-chess 1.11**: `Score.cp`/`Score.mate` are type-dependent attributes —
  use the `score()`/`mate()` methods. `Board.push()` does not validate the side
  to move. No `chess.rotate_180` / `chess.BB_VOID` — compose
  `transform(chess.flip_vertical).transform(chess.flip_horizontal)` (squares
  map `sq ^ 63`). `transform()` keeps castling rights unchanged (wrong for
  rotations — set to 0), and `fen()` silently sanitizes castling rights that
  don't match king placement.
- **Django JSONField**: in-place mutation (`data['x'] = y`) is not tracked —
  reassign the whole dict before save.
- **OCR warmup ~2min per session**; each `import_pages` invocation pays it —
  batch sizes trade overhead vs resumability. `--skip-existing` makes re-runs
  safe.
- **tsoj orientation model** (`auto_rotate_board`) normalizes diagrams to
  White's perspective; misjudgement → stored FEN = truth rotated 180°.
- **`number` is text**: lexicographic sort is wrong ("10" < "9") — always cast.

## 8. Common commands

```bash
# server
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
pkill -f "manage.py runserver"

# import / scan
.venv/bin/python manage.py import_pages polgar-5334 uploaded-books/Polgar-5334.pdf \
  --page-number-start 226 --skip-existing --log-file tmp/import.log
.venv/bin/python manage.py set_layout <slug> <pdf> <page>
.venv/bin/python manage.py calibrate_book <slug> <page_photo>
.venv/bin/python manage.py export_puzzles <slug>

# tests
.venv/bin/python -m pytest books/tests/   # CV round-trips, views, commands, models
```

Debug helpers: `scripts/debug_*.py` print warps/masks as ASCII for CV tuning;
`scripts/autocalibrate_polgar.py`, `scripts/ocr_lib_probe.py`.
