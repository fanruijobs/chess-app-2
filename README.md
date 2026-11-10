# Chess Trainer

A personal chess-improvement tool: download your chess.com games, analyze them
with Stockfish, find the **turning points** (the first mistake per game that throws away
an equal-or-winning position, costing more than 2.0 in evaluation), get a classification and an optional LLM explanation
of each mistake, browse everything in a web UI, and export printable PDFs —
including a **masked training mode** (positions only, answers at the end).
It also turns scanned puzzle books (e.g. Polgar's 5334) into a reviewable,
trainable puzzle database.

Docs: **[design.md](design.md)** (API / DB / UI design) ·
**[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)** (current state, gotchas, cleanup
commands).

## Setup

```bash
brew install stockfish pango          # engine + WeasyPrint's system deps
python3 -m venv .venv                 # use Homebrew Python (needed for WeasyPrint)
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python manage.py migrate

# access control: all app pages require login (no signup). The migration
# creates `fanrui89`, owner of every training set that existed at the time.
.venv/bin/python manage.py createsuperuser   # e.g. admin
```

Optional: copy `.env.example` to `.env` and set `DEEPSEEK_API_KEY` to enable
LLM explanations of turning points (DeepSeek API, ~$0.001 per explanation).

## CLI

```bash
# fetch recent games of a username (chess.com public API, serial requests)
.venv/bin/python manage.py fetch_games <username> [--months 3] [--limit N] [--rated-only] [--dry-run]

# queue games for analysis; per-time-class profiles by default
# (bullet 14/250, blitz 16/200, rapid 18/150, daily 20/120 — depth/recorded
# threshold; selection itself uses the fixed 2.0-drop rule)
.venv/bin/python manage.py queue_games <username> [--lost-only] [--time-class rapid,blitz] [--depth N] [--threshold N] [--priority N]

# run the worker: claims queued jobs with one shared Stockfish process.
# daemon mode runs until interrupted; --once is cron/launchd-friendly.
.venv/bin/python manage.py analyze_worker [--once] [--max-jobs N] [--llm] [--threads 4]

# analyze immediately (bypasses the queue); --lost-only restricts to lost games
# (target is a username or comma-separated game uuids)
.venv/bin/python manage.py analyze_games <username> [--lost-only] [--time-class rapid] [--threshold 150] [--depth 18] [--force] [--limit N] [--llm]

# generate DeepSeek explanations for turning points (idempotent)
.venv/bin/python manage.py enrich_turning_points <username|uuids> [--force] [--limit N]

# printable PDF of turning points: 6 positions per A4 page (2 columns x 3 rows)
.venv/bin/python manage.py export_pdf <username|uuids> [--lost-only] [--time-class rapid] \
    [--from 2026-08-01] [--to 2026-08-31] [--masked] [--out file.pdf]

# train sets: the set page has an Export PDF button — all positions, 6 per
# page, chess.com-style borderless boards with the best move under each

# load a book's puzzles straight from a CSV of FENs (no scan/OCR) — for books
# that ship a machine-readable list (e.g. corrected Woodpecker-2 exercises);
# re-running matches on (book, number) and updates in place
.venv/bin/python manage.py load_puzzles_csv <slug> puzzles.csv [--title "..."] [--status extracted] [--dry-run]
```

`--masked` produces a training sheet: the grid shows only the position and
your move ("what should you have played?"); reasons, best moves and
continuations are collected in an answer key at the end of the document.

Scheduling: `analyze_worker` is the long-running daemon (start it once, it
works through the queue and reports progress/heartbeat to the web UI). For
time-windowed runs instead, use cron/launchd with `analyze_worker --once
--max-jobs N`. A worker killed mid-game leaves its job `running`; the next
start re-queues stale jobs automatically.

## Web app

```bash
.venv/bin/python manage.py runserver
```

All pages require login (redirecting to `/accounts/login/` — the admin login
only accepts staff, so it can't be the shared entry point). Users sign in at
`/accounts/login/`, change their own password at `/accounts/password_change/`,
and are managed (create/deactivate/reset) through the Django admin at
`/admin/`.

Training sets are **per-user**: a set belongs to one owner and is only visible
to them. Superusers see every set. Marks ride along with the set, so
transferring ownership keeps the training history.

- `/` — players + fetch form
- `/players/<username>/` — games grouped by play date with result/type/date
  range/analyzed filters, per-type stats (games/losses per
  bullet/blitz/rapid/daily), queue form and live queue + worker status,
  and an **export form** (date range, game type, lost-only, masked training mode)
- `/games/<uuid>/` — board with move navigation, eval graph (red dots mark
  turning points, dashed line marks the threshold), turning-point list with
  classifications, LLM explanations (per-item regenerate button), PDF export
- `/analysis/` — a standalone **analysis board** with no server round-trips:
  analyze from the start position (live, re-analyzing after every move) or a
  **custom position** (click-to-place piece palette, clear/start/flip, FEN
  box); Stockfish runs in the browser (see below)
- `/rework/` — every Re-work training position across sets (filter by
  training set or book), linked into the training session
- `/bad-fens/` — every puzzle whose stored FEN is illegal (malformed,
  missing/extra kings, >32 pieces), per-book filter, each row linked into
  the review editor
- `/admin/` — browse all models

Web "Queue" buttons enqueue jobs; the `analyze_worker` daemon processes them
(a game takes ~1–2 minutes at depth 18). If the worker isn't running the pages
tell you and nothing gets processed.

### Interactive boards (training session + analysis board)

Both interactive boards use **Chessground** (vendored build) for rendering,
**chess.js** for position state / legal-move enforcement, and **Stockfish
compiled to JavaScript** running in a web worker — no backend analysis
traffic. The engine (`stockfish.js`, npm 10.0.2) loads from **jsDelivr**,
with the vendored copy (`books/static/books/vendor/stockfish.js`) as an
offline fallback, through a same-origin glue worker (browsers forbid
cross-origin workers). Searches are `go depth 25 movetime 1000` with
MultiPV 3. Gotchas of the vendored chessground build: `movable.dests` must
be a `Map`, and the `after` move callback lives under
`movable.events.after` (not top-level `events`).

## Tests

```bash
.venv/bin/python -m pytest                        # unit tests (fast; excludes integration + e2e)
.venv/bin/python -m pytest -m integration         # real-Stockfish integration tests
.venv/bin/python -m pytest -m ""                  # include integration tests (not e2e)
.venv/bin/python -m pytest --cov=chessdata --cov=analysis --cov=books --cov=training --cov-report=term

# browser end-to-end tests
.venv/bin/pip install -r requirements-e2e.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/e2e -m e2e
```

App-code coverage is ~74% with the full unit suite (core logic: turning-point
predicate, classifiers, queue, LLM client, sync service, PDF layout).

CI/CD lives in `.github/workflows/`; see `.github/workflows/README.md` for
required secrets and server setup.

## Puzzle books (scan -> train)

Two recognition backends: a local CV pipeline (per-book calibration) and an
**OCR bridge** to [tsoj/Chess_diagram_to_FEN](https://github.com/tsoj/Chess_diagram_to_FEN)
(a PyTorch CNN trained on book diagrams — much more accurate on real scans).

### OCR bridge setup (once, ~2 GB)

```bash
brew install python@3.13          # the OCR lib needs <= 3.13 (no 3.14 wheels)
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv .venv313
.venv313/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv313/bin/pip install pyfastnoiselite
.venv313/bin/pip install git+https://github.com/tsoj/Chess_diagram_to_FEN.git
git clone --depth 1 --branch 1.0 https://github.com/tsoj/Chess_diagram_to_FEN.git /tmp/chess_diagram_to_fen_v1
(cd /tmp/chess_diagram_to_fen_v1 && curl -sL -o models.zip \
  https://github.com/tsoj/Chess_diagram_to_FEN/releases/download/1.0/models.zip \
  && unzip -o -q models.zip && rm models.zip)
```

### Woodpecker-style books (line-drawn boards, printed numbers)

Woodpecker Method volumes: white line-drawn boards with a SOLID BLACK FRAME,
the printed number left of each board's top-left corner, and a side-to-move
TRIANGLE right of the board (filled downward at the top = black to move,
outlined upward at the bottom = white). Left/right pages have mirrored
margins, so the slot grid is detected per page from the solid frame lines
(`books/cv/steps.py` `line_slots`, profile `steps.board_style = "line"`);
`steps.turn_markers` rewrites the OCR FEN's turn from the triangle.

```bash
# woodpecker-1 is fully imported: 1128 puzzles (#1-1128, pages 32-221),
# 401 black-to-move / 727 white-to-move. Printed-number OCR reads 1-2 digit
# numbers reliably; 3-4 digit numbers are repaired by the arithmetic formula
# (printed pages 69 and 197 = PDF 70 and 198 are chapter dividers — the
# numbering skips 6 each). Note a re-import re-reads the printed numbers, so
# strip `number_templates` from the profile first to keep the arithmetic
# numbers (see tmp/profile_templates.py), and re-check every page image
# against the PDF afterwards.
.venv/bin/python manage.py import_pages woodpecker-1 uploaded-books/woodpecker-1.pdf \
    --page-number-start 33 --limit 190 --skip-existing
```

### Polgar-5334-style books (fixed page layout)

```bash
# 1. calibrate the page layout from one clean page (2x3 slots)
.venv/bin/python manage.py set_layout polgar-5334 uploaded-books/Polgar-5334.pdf 21

# 2. store the book's first puzzle page (auto-numbering base)
.venv/bin/python manage.py shell -c "
from books.models import Book
p = Book.objects.get(slug='polgar-5334').calibration
p.data = {**p.data, 'first_page': 16}; p.save(update_fields=['data', 'updated_at'])"

# 3. import (OCR per slot, auto-numbered puzzle N = Nth diagram)
.venv/bin/python manage.py import_pages polgar-5334 uploaded-books/Polgar-5334.pdf \
    --page-number-start 16 --skip-existing
```

`import_pages` is resumable: Ctrl+C anytime and re-run the same line
(`--skip-existing` skips imported pages). Add `--log-file tmp/import.log` for
a persistent log — per-page progress, failures (a bad page logs `FAILED` and
the batch continues) and a `batch done: … failed=N` summary. The first OCR
call of a run pays a ~2 min model-load warmup, then ~9 s/page.

### Steps-workbook books (solid-fill boards, fixed 3x4 grid)

Steps-series pages print 12 solid-fill diagrams per page (3 columns x 4
rows); artist drawings may occupy slots; numbering is positional
`p<page>-<slot>` (e.g. `p007-03`, right-then-down; drawing slots are skipped,
never renumbered):

```bash
.venv/bin/python manage.py set_layout --steps <slug> <pdf> <puzzle-page> \
    [--first-page N]   # the PDF page printed as "1" (variable front matter)
.venv/bin/python manage.py import_pages <slug> <pdf> --skip-existing
```

The steps pipeline detects the slot grid per page (with a stored-layout
fallback), gates slots as boards via checkerboard correlation + square gap
lines (both branches additionally require the dark squares to form a
checkerboard — this rejects 5x5 sub-board diagrams), retries shifted crops,
and finally lets the OCR localize the board inside the whole band region —
which recovers boards that sit off the detected grid. Currently imported:
`step-6-extra` (Steps 6 Workbook Extra, ~1300 puzzles). The plain `step-6`
scan was too degraded (vignette, tilted boards, lost square structure) and
was dropped — re-scan for that one.

### Horsehead-style books (printed numbers + skipped 5x5 chapters)

`horsehead` (Horsehead, 176 pages, unified numbering 1-1182) is imported with
numbering mode `"printed"`: the printed number above each board's top-left
corner is authoritative (digit templates bootstrapped from labeled pages,
band geometry in `profile["number_band"]`) — unreadable numbers stay empty
rather than being positionally guessed. Its chapter-opener pages print 5x5
(d4-h8) sub-boards that share the numbering sequence and are skipped by the
board gate; piece-heavy pages whose grids defeat the gate are listed in
`profile["ocr_only_pages"]` with hand-set layouts. Final state: **1014
puzzles**, the 14 skipped 5x5 chapters account for the other 168 numbers
(1014 + 168 = 1182).

The CV pipeline remains for books without a fixed layout:

```bash
.venv/bin/python manage.py calibrate_book <slug> page_photo.jpg --baseline \
    --label "L3=wN" --label "D7=bQ" ...   # cluster ids from the printed table
.venv/bin/python manage.py import_pages <slug> pages/ --page-number-start 1
```

**Side to move**: the OCR bridge normalizes every diagram to White's
perspective, so the printed side-to-move is recovered afterwards from a
curated list (`b2mov.txt` style: puzzle numbers for Polgar-style books,
`p<page>|<slots>` lines for steps books) and applied by flipping the stored
FEN's turn. Marked so far: `polgar-5334` 762 black-to-move puzzles,
`step-6-extra` 279, `horsehead` #896–903 + #1112–1182.

### AI page parser (DeepSeek vision, evaluation-only)

`scripts/ai_parse_pages.py` sends a whole page image to DeepSeek's vision
model and gets back every diagram's FEN in one JSON answer — an evaluation
of vision-LLM parsing against the local OCR/CV pipeline. It never writes
the DB: results land in a JSONL cache (`tmp/ai_parse_<slug>.jsonl`) and a
per-page report compares each slot with the stored puzzles (FULL /
PLACEMENT / DIFF(n), side-to-move, missing on either side). Layout
fractions from the book's calibration profile anchor the model's row/col to
slot numbers.

```bash
.venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5 --dry-run
.venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --smoke-test   # one page; exit 0 only on valid FENs
.venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5      # batch
.venv/bin/python scripts/ai_parse_pages.py --book step-6-extra --start 88 --count 5 --report-only
```

Model: `deepseek-v4-flash-vision-exp` (the `deepseek-chat` used by the
turning-point explanations is text-only). Gotchas that have bitten this
script: it is a reasoning model, so `max_tokens` covers reasoning + answer
and must be ~32K or the answer comes back empty (a `length` finish is
raised as an error); pages are sent as JPEG not PNG (large PNGs
intermittently fail with "Image not accessible"); if every diagram comes
back as the same position the answer is treated as a transcription failure
and one corrective retry is made.

Then review + verify positions in the web UI (`/review/`), build training
sets at `/train-sets/` (see below), and export PGN (`export_puzzles`).

- `/books/` — books with puzzle/verified/review counts and calibration status
- `/books/<slug>/` — puzzles ordered numerically (30/page; steps books order
  by page/slot), `?page=N` filters to one scanned page, and two
  direct-navigation boxes: jump to a puzzle number (`?goto=N`, loose forms
  like `7-3` accepted on steps books) or to a page — for steps books the
  page box jumps to the **scanned page**, otherwise to a pagination page
  (`?p=N`). "Download page N as PDF" exports **6 puzzles per page** in a
  fixed 2×3 grid: compact title zone, identical board sizes across pages,
  no FENs, black-to-move marked as `#p007-01(B)`
- `/puzzles/<pk>/` — the puzzle board plus persistent fixes (admin only):
  **Fix orientation** rotates the notation 180° and saves (for scans the OCR
  read from the wrong side), the **to-move switch** flips the side to move in
  the stored FEN and saves, and **prev/next buttons** walk through the book's
  puzzles in its own order (numeric number for printed books, page/slot for
  steps books); "flip board" is view-only
- `/review/` — extraction review queue (lowest confidence first); click-to-fix
  the board, record the book's own puzzle number, verify/reject
- `/bad-fens/` — every illegal stored FEN (malformed, missing/extra kings,
  >32 pieces), per-book filter, each row linked into the review editor
- `/train-sets/` — training sets that **mix any number of sources**: book
  ranges (page and/or number), game turning points (player + date range),
  and the Re-work positions of other sets; existing sets can also be
  **cloned** from the creation page. The session board is interactive:
  **only legal moves are movable** (chess.js), a White/Black-to-move
  indicator, flip / fix-orientation / switch-side-to-move buttons (the latter
  two persist to the item's FEN and are admin-only), a **position editor** for
  repairing ill-scanned boards (admin only; palette click/drag or paste a FEN;
  saving also fixes the source puzzle in the book), an opt-in **Analysis toggle
  button** (blue; green "Hide Analysis" while the lines are shown) that
  runs Stockfish in the browser — up to 3 continuations, 1 second or
  depth 18 per position, refreshed after every move — and an **Open in
  Chess.com** link that carries the current FEN to chess.com's analysis
  board. Mark each
  position **pass / re-work** (or a whole category) and see results by
  category (Pending / Re-work / Passed) on the set page; an **Export PDF**
  button downloads all positions of the set (6 per A4 page, chess.com-style
  borderless boards, best move shown)
- `/rework/` — every Re-work position across all sets (filter by set or
  book); rows open the training session with `?status=rework` so next/prev
  stays in the category

Calibration is per book (each book uses one piece font); the profile drift
warning flags re-scans with different lighting. Diagram-to-number association
is manual in v1 (fill the book's own numbering during review).

## Notes

- The chess.com public API exposes only public games; hidden profiles return
  403. Requests must be serial (parallel requests get rate-limited).
- Non-standard variants (crazyhouse, chess960, ...) are skipped by default
  because standard chess evaluation doesn't apply to them.
- Turning point = the first of your moves in a game where the evaluation
  (from your perspective, mate scores included) drops by more than 2.0 pawns
  from an equal-or-better position (not worse than -1.5) — at most one per game.
