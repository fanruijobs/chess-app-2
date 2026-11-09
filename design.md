# Design — Chess Trainer

API, database, and UI design. For current project state and gotchas see
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md); for usage see [README.md](README.md).

## 1. Architecture overview

Single-user Django app, **SQLite**, server-rendered templates, **no client-side
JavaScript**. Three bounded domains:

```
chessdata   chess.com sync: Player -> Game -> MoveEval
analysis    engine analysis: AnalysisJob/AnalysisRun -> TurningPoint (+ LLM, PDF)
books       puzzle books: Book -> PageImage -> Puzzle
training     training sets: TrainingSet -> TrainingSetItem -> TrainingAttempt
```

All mutations are plain HTML form POSTs or JSON endpoints (training analyze/mark);
all reads are GETs that re-render the page. `books/` additionally shells out
to a separate Python-3.13 venv for diagram OCR (see README).

## 2. API design

### 2.1 Conventions

- **Auth**: every page is `@login_required`; anonymous requests redirect to
  `/accounts/login/` (the admin login only accepts staff). No signup — users
  are created in the Django admin. Training sets are per-user: one `owner`
  FK, enforced through `sets_for()`/`set_for()` in `training/services.py`;
  superusers see every set. Marks live on the set's items, so they follow the
  set to its owner.
- **No JS**: mutations POST a hidden `action` field + `{% csrf_token %}`;
  handlers redirect back (`redirect` after POST). The JSON APIs are the
  training endpoints (`analyze`, `mark`) — the rest is form POSTs.
- **Feedback**: Django messages framework rendered in the base template.
- **Media**: page images served from `media/` via static() in DEBUG.
- Legacy GET params are harmless and preserved where meaningful (see `?turn=`).

### 2.2 Routes

**analysis** (root):

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | players list + fetch form |
| `/players/fetch/` | POST | `action=fetch`, chess.com sync (serial, rate-limited) |
| `/players/<username>/` | GET/POST | games by date, filters, stats; POST queues jobs / exports |
| `/games/<uuid>/` | GET | board, eval graph, turning points, per-item LLM regenerate |
| `/games/<uuid>/pdf/` | GET | printable PDF (masked mode via query) |
| `/games/<uuid>/turning-points/<tp_id>/llm/` | POST | regenerate one LLM explanation |

**books** (`books/…`):

| Route | Method | Purpose |
|---|---|---|
| `/books/` | GET | book index with puzzle/verified/review counts |
| `/books/<slug>/` | GET | paginated puzzle list; see query params below |
| `/books/<slug>/pages/<n>/pdf/` | GET | one scanned page rendered as PDF |
| `/review/` | GET | review queue, lowest confidence first; `?book=<slug>` filter |
| `/review/<pk>/` | GET/POST | edit/verify/reject one puzzle (actions below) |
| `/pages/<page_id>/diagram/<index>.png` | GET | source diagram crop (PNG, +15% margin) |
| `/puzzles/<pk>/` | GET/POST | puzzle board; POST actions below |
| `/puzzles/<pk>/board.svg` | GET | inline SVG board (server-rendered) |
| `/train-sets/` | GET/POST | list + create training sets |
| `/train-sets/<id>/` | GET | set detail = training results (counts, pass rate, mark-all) |
| `/train-sets/<id>/pdf/` | GET | all positions of the set as printable PDF (6 per page) |
| `/train-sets/<id>/items/<n>/` | GET | session: free-move chessground board |
| `…/items/<n>/analyze/` | POST | JSON `{fen, moves, depth?, time?}` → `{fen, lines[{pv, san, eval_cp, eval_mate}]}` (MultiPV 3, on the fly) |
| `…/items/<n>/mark/` | POST | JSON `{result: passed|again}` → `{ok, item_status, attempts, next_item_url, stats}` |
| `/train-sets/<id>/mark-all/` | POST | mark all pending items |

### 2.3 Query parameters (book detail)

| Param | Meaning |
|---|---|
| `p=N` | pagination page (30 puzzles/page, ordered **numerically** by number) |
| `page=N` | filter to one scanned page's puzzles |
| `goto=N` | jump directly to puzzle number N → 302 to its detail page |

Puzzle view: `flip=1` flips the display orientation only; `turn=w|b` is a
legacy display-only override (the to-move switch now persists instead).

### 2.4 POST actions

| View | `action` | Effect |
|---|---|---|
| review | `verify` | validate FEN, save FEN + number, mark VERIFIED (confidence 1.0) |
| review | `save` | save FEN + number, stays unverified |
| review | `reject` | mark REJECTED |
| review | `rerun` | re-extract from the source page image |
| puzzle | `rotate` | **persist**: rotate position 180° (notation), rotate `piece_cells`, drop castling rights |
| puzzle | `toggle_turn` | **persist**: flip side-to-move in the stored FEN |
| training mark | JSON `{result, status}` | mark item passed/rework, bump counters, return next in category |
| training analyze | JSON `{fen, moves, depth?, time?}` | MultiPV lines for the current board |

## 3. Database design

### 3.1 chessdata

- **Player**: `username` (unique, lowercase), `last_synced_at`, `last_archive_url`
- **Game**: PK = chess.com uuid; headers (white/black/result/end time/time
  class), FEN, PGN, `analyzed` flag, `last_run` → AnalysisRun
- **MoveEval**: per-ply engine evaluation of a game (cp or mate, depth)

### 3.2 analysis

- **AnalysisJob**: queue row → `analyze_worker`. Fields: game, `depth`,
  `threshold_cp`, `priority` (higher first), status (queued/running/done/
  failed/cancelled). Unique: at most one *active* job per game.
- **AnalysisRun**: one engine pass over a game (engine name/version, depth,
  threshold used, status, plies). `Game.last_run` → latest.
- **TurningPoint**: the FIRST user mistake per game that threw away a decent
  position — eval before the move ≥ -1.5 (equal or better) and a drop of
  more than 2.0 pawns. At most one per game; `run.threshold_cp` is recorded
  metadata only.
  `ply`, `move_number` ("23... Nf6"), `san_played`/`san_best`, `fen_before`/
  `fen_after`, evals (cp or mate, nullable), `drop_cp`, `classification`
  (TextChoices mirroring `analysis/classifiers.py`), `rule_text`, LLM fields
  (`llm_status` pending/done/failed, `llm_text`, `llm_model`, `llm_error`).
  Unique (game, ply).
- **WorkerHeartbeat**: single row (pk=1), daemon liveness.

### 3.3 books

```
Book 1───N PageImage 1───N Puzzle N───1 Book
Book 1───1 CalibrationProfile
```
training:
TrainingSet 1───N TrainingSetItem 1───N TrainingAttempt
```

- **Book**: title, slug (unique), description, calibration status
  (none/pending/ready).
- **PageImage**: book, `page_number` (nullable), `source_file`
  ("book.pdf#page=34"), image + thumbnail, width/height, status
  (new/processed/failed), error, `extracted_at`. Unique (book, page_number).
  Re-processing a page **updates the existing row** (upsert by page_number,
  then source_file).
- **CalibrationProfile**: 1:1 with Book. `data` JSON: CV calibration
  (clusters/templates/empty/solidity_cut/features/source) and/or OCR layout
  (`layout` slot grid as page fractions, `first_page` numbering base,
  `ignore_pages`). Django JSONField — reassign the whole dict to save.
- **Puzzle**: book, page (nullable), `diagram_index` (slot), `number`
  (**CharField** — the book's own numbering verbatim; cast to int for numeric
  work), `fen`, status (extracted/review/verified/rejected), confidence,
  `diagram_bbox`, `piece_cells` (JSON, 64 entries rank-8-first), notes;
  training counters (attempts/solved/failed, `last_trained_at` — updated by
  the training app on mark). Unique (book, number) when non-empty;
  unique (book, page, diagram_index); index (book, status). Ordering
  (book, number, id).
- **TrainingSet / TrainingSetItem / TrainingAttempt** (app `training`): sets
  of positions snapshotted from book puzzles or turning points (FEN copied at
  creation); one `owner` per set (PROTECT — per-user visibility, §2.1).
  Sets are **source-agnostic** — one set can mix any number of
  book ranges, turning-point ranges, and the **Re-work category of other
  sets** (`filters` = list of entry specs), and existing sets can be extended
  with more entries anytime (`add_entries`). User-marked categories:
  `pending` / `passed` / `rework` (a marked failure lands in the Re-work
  category, which is trainable on its own) — no stored solution lines; the
  engine analyzes on demand. Item source FKs cascade: deleting a book or
  turning point removes its items (the XOR single-source constraint holds).

### 3.4a Line-drawn boards (woodpecker style)

White line-drawn boards (faint dotted inner grid) in a SOLID BLACK FRAME:
left/right pages drift (mirrored margins), so `books/cv/steps.py`
`line_slots` finds the grid PER PAGE from full-extent solid frame lines
(page-column dark fraction + board-length run check; captions/headers
measure <= 40px runs vs a frame's ~406px). Rows come from the first column
strip's profile (cut 0.8). `is_line_board` gates each slot (frame at the
crop edges, white-board ink range). Profile: `steps.board_style = "line"`,
`steps.turn_markers = true` — the turn comes from the triangle right of
the board (filled top = black, outlined bottom = white; connected
components hugging the frame, the neighbour's printed number sits away
from the frame and is ignored). Printed numbers are read from the LEFT
gap band (`number_band`, digit templates bootstrapped from the arithmetic
ground truth); woodpecker's 3-4 digit numbers fragment/merge and are
repaired post-import by the arithmetic formula (printed = pdf - 1,
column-major position; printed 69/197 are divider pages — numbering
skips 6 at each).

### 3.4 Status flows


```
Puzzle: extracted --low confidence/illegal--> review --human verify--> verified
                                              \-- human reject--> rejected
Imports never touch verified puzzles (--force overrides); upserts preserve ids.
PageImage: new -> processed | failed
AnalysisJob: queued -> running -> done | failed ; cancelled
```

## 4. UI design

### 4.1 Design principles

- **Server-rendered everywhere**: boards are inline SVG (`chess.svg`), page
  changes are full reloads, mutations are POST forms — works with JS off.
- **Buttons, not navbars**: every action is a visible `.button` (blue) or a
  link; the session's Analysis toggle is the one state-colored button
  (green while its lines are live); destructive-ish fixes are explicit
  ("… (saves)").
- **Badges** for statuses (`badge-verified`, `badge-review`, …).
- **Messages** (`<ul class="messages">`) confirm every mutation.
- Minimal CSS (`books/static/books/style.css` + vendor), CSS variables for
  colors, a `trainer-layout` grid that collapses on <900px.

### 4.2 Pages

| Page | Layout |
|---|---|
| `/` players | fetch form + player table |
| player detail | date-grouped games, filters, stats, queue/worker status, export form |
| game detail | board + move navigation, eval graph (turning points in red, threshold dashed), classified TP list with LLM text + per-item regenerate |
| `/books/` | book list with counts + calibration badge |
| book detail | stats line; pagination row (prev/next) **+ two nav forms** (puzzle # → goto, page # → p); puzzle table (number link, status, confidence, attempts/solved/failed); pages table below |
| puzzle view | SVG board; row of controls: *Flip board* (view-only), *to-move ⇄ switch (saves)*, *Fix orientation: rotate 180° (saves)*; FEN + stats side panel |
| review queue | lowest-confidence first, per-puzzle edit links, per-book filter |
| review puzzle | diagram crop + click-to-fix board (piece buttons), FEN text field, number field, verify/save/reject/rerun |
| book page PDF | **6 puzzles per page**, fixed 2×3 grid with a compact title zone; boards identical across pages; no FENs; black-to-move numbered `#p007-01(B)` |
| train set PDF | same 6-per-page layout; chess.com-style **borderless** boards (ivory `#ebecd0` / green `#739552`); label + `(B)` mark + best move under each board; unparseable FENs skipped |
| training set list | create form with **"add another source" rows** — book range, turning-point range, or another set's Re-work positions, mixed freely |
| training set detail | category tabs (Pending / Re-work / Passed) = the results page (counts, pass rate, per-item attempts); per-category training + mark-all; "Add positions" section extends the set; **Export PDF** button (all positions, in set order) |
| training session | free-move chessground board; **Analysis** toggle button (blue when off, green "Hide Analysis" on) for MultiPV lines as arrows + text; **Open in Chess.com** link (current FEN in the URL); Pass/Re-work buttons, auto-advance within the active category |

### 4.3 Interaction notes

- Book pagination is **numeric by puzzle number** (not id) — except steps
  books (`p001-01`-style numbers), which order by scanned page + slot:
  unnumbered strays sort first — cleanup deletes them.
- Training items are **snapshots** (FEN copied at set creation) and sets are
  source-agnostic; marking a position *re-work* puts it in the Re-work
  category, which is itself a valid source for other sets.
- All book positions are eligible for training sets — the verified/review
  status distinction does not gate them (the user is the judge).
- "Fix orientation" exists because the OCR orientation model sometimes reads a
  white-at-bottom scan as black's perspective (stored FEN = truth rotated
  180°). The button rotates back and is self-inverse.
- The to-move switch and orientation fix are the only puzzle-view controls
  that write to the DB; everything else is display-only.
