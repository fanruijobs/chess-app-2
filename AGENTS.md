# AGENTS.md

Guidance for AI coding agents working on **Chess Trainer**.

Read these first — they are the domain source of truth:

| Doc | Contents |
|---|---|
| `README.md` | Setup, CLI, web UI, book-import workflows |
| `PROJECT_CONTEXT.md` | Current data state, hard-won gotchas, cleanup commands |
| `design.md` | Routes, API, database and UI design |
| `deploy/README.md` | Production topology and deploy steps |

When docs and code disagree, trust the code and update the docs.

## What this project is

A personal Django 6.1 web app for chess improvement, with two domains:

1. **Game analysis** — sync chess.com games, analyze with Stockfish, record
   evaluation curves, detect one *turning point* per game (the first mistake
   that throws away an equal-or-better position), classify it, optionally
   generate DeepSeek explanations, and export printable/masked PDFs.
2. **Puzzle books and training** — turn scanned chess books into a reviewable
   puzzle database via CV/OCR, then build per-user training sets that mix book
   puzzles with game turning points and re-work items.

The UI is deliberately **server-rendered and no-JS** except for the
interactive boards (Chessground + chess.js + Stockfish WASM in a Web Worker).
There is no npm/build pipeline.

## Non-negotiables

- **Do not deploy, restart, or run destructive commands on the production
  server unless the user explicitly asks.** This includes `systemctl restart`,
  `manage.py flush`, `loaddata`, and database dumps over the live DB.
- **Never commit secrets or large generated data**: `.env`, `db.sqlite3`,
  `media/`, `uploaded-books/`, `staticfiles/`, `tmp/`, `*.log`, exported PDFs.
- **Keep the no-JS architecture.** Use Django templates, normal form POSTs +
  `{% csrf_token %}`, redirect-after-POST, and the messages framework. The
  training session and analysis board are the only interactive JS pages.
- **Scope every training-set access through `sets_for()` / `set_for()`**
  (`training/services.py`). A set is visible only to its owner; superusers see
  all. Never query `TrainingSet.objects` directly in a view/endpoint.
- **Run the relevant tests after a change** (see Testing below).

## Setup and local run

### Windows

The default `python` on some machines is 3.9, which is too old for Django 6.1
(Python 3.12+ is required). Use Python 3.14 via the launcher:

```powershell
cd C:\Users\ricky\workspace\chess-app
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser   # first run only
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8888
```

Open `http://127.0.0.1:8888/`; all pages require login. `createsuperuser`
makes the first local account (there is no signup page).

### macOS / Linux

```bash
brew install stockfish pango          # engine + WeasyPrint native deps
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver 127.0.0.1:8888
```

`config.settings` is the dev baseline: `DEBUG=True`, SQLite at
`db.sqlite3`, Stockfish at `/opt/homebrew/bin/stockfish`. The production
settings live in `config/settings_deploy.py` and are selected only with
`DJANGO_SETTINGS_MODULE=config.settings_deploy`.

### Environment variables

Loaded from `.env` by `config/settings.py` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | Optional; enables LLM turning-point explanations |
| `STOCKFISH_PATH` | Path to the Stockfish binary; set it on Windows/Linux |
| `CHESS_COM_USER_AGENT` | Contact string for chess.com API requests |
| `POSTGRES_DB/USER/PASSWORD/PORT` | Deploy settings only |
| `BOOKS_OCR_REPO` | Path to the tsoj OCR repo clone (default `/tmp/chess_diagram_to_fen_v1`) |
| `BOOKS_OCR_PYTHON` | OCR interpreter (default `.venv313/bin/python`) |

### WeasyPrint on Windows

WeasyPrint requires GTK/Pango native libraries at import time. The PDF modules
import `weasyprint` **lazily, inside the render functions**, so the web app
starts and all non-PDF features work without GTK. Do not move that import back
to module top level. PDF export/tests still need GTK/Pango installed.

## Repository map

| Path | Responsibility |
|---|---|
| `config/` | Settings, root URLs, WSGI/ASGI |
| `chessdata/` | `Player`, `Game`, `MoveEval`; chess.com sync (`services/chesscom.py`) and `fetch_games` |
| `analysis/` | `AnalysisJob`, `AnalysisRun`, `TurningPoint`, `WorkerHeartbeat`; Stockfish engine, classifiers, queue/worker, LLM, PDFs |
| `books/` | `Book`, `PageImage`, `CalibrationProfile`, `Puzzle`; CV pipeline (`cv/`), OCR bridge (`ocr.py`), import/verify/export commands |
| `training/` | `TrainingSet`, `TrainingSetItem`, `TrainingAttempt`; set construction, session/mark views, PDF export |
| `deploy/` | systemd unit, nginx config, deployment runbook |
| `scripts/` | Standalone OCR/AI-page-parse and CV debugging helpers |

Key module pointers:

- `analysis/classifiers.py` — rule registry; labels mirror
  `TurningPoint.Classification` 1:1.
- `analysis/turning_points.py` — the turning-point predicate.
- `analysis/profiles.py` — per-time-class depth/threshold defaults.
- `analysis/queue.py`, `analysis/engine.py` — job queue and Stockfish wrapper.
- `books/cv/` — generic CV pipeline; `books/cv/steps.py` — Steps-workbook path.
- `books/services.py` — puzzle import/store/upsert logic.
- `training/services.py` — `create_set`, `add_entries`, `clone_set`,
  `mark_item`, `mark_all`, `next_item`, `sets_for`, `set_for`.

## Commands

All commands assume the venv's Python (`.\.venv\Scripts\python.exe` on
Windows, `.venv/bin/python` elsewhere).

### Web and tests

```bash
python manage.py migrate
python manage.py runserver 127.0.0.1:8888
python manage.py collectstatic --noinput

pytest                                   # fast suite; integration tests excluded
pytest -m ""                             # include real-Stockfish integration tests
pytest training/tests/ -q
pytest --cov=chessdata --cov=analysis --cov-report=term
```

`pytest.ini` sets `DJANGO_SETTINGS_MODULE=config.settings` and
`addopts = -m "not integration"`. Mark tests that need a real Stockfish
binary with `@pytest.mark.integration`. PDF tests need WeasyPrint's native
libraries.

### Game analysis pipeline

```bash
python manage.py fetch_games <username> [--months 3] [--limit N] [--rated-only]
python manage.py queue_games <username> [--lost-only] [--time-class rapid,blitz]
python manage.py analyze_worker [--once] [--max-jobs N] [--llm] [--threads 4]
python manage.py analyze_games <username> [--lost-only] [--depth 18] [--force]
python manage.py enrich_turning_points <username|uuids> [--force]
python manage.py export_pdf <username|uuids> [--masked] [--out file.pdf]
```

### Book import

```bash
# fixed page layout (Polgar-style)
python manage.py set_layout <slug> <pdf> <page>
python manage.py import_pages <slug> <pdf> --page-number-start 16 --skip-existing

# Steps-workbook layout
python manage.py set_layout --steps <slug> <pdf> <page> [--first-page N]
python manage.py import_pages <slug> <pdf> --skip-existing

# generic CV calibration
python manage.py calibrate_book <slug> <page_photo.jpg> --baseline --label "L3=wN"
python manage.py import_pages <slug> pages/ --page-number-start 1

python manage.py export_puzzles <slug>
python manage.py load_puzzles_csv <slug> puzzles.csv [--dry-run]
```

The OCR bridge needs a separate `.venv313` (Python ≤ 3.13) with
`tsoj/Chess_diagram_to_FEN` at tag `1.0`; see `README.md` for the full setup.
First OCR call per process pays a ~2 minute torch model-load warmup.

## Domain invariants and gotchas

- **`Puzzle.number` is text**, not an integer. Never sort or compare it
  lexicographically; use `Cast("number", output_field=IntegerField())`.
  It is unique per book only when non-empty.
- **`Puzzle.piece_cells`** is JSON with 64 entries in **rank-8-first** order
  (h8 … a1), cluster ids or `None`; the OCR path leaves it `[]`.
- **`TrainingSetItem.fen` is a snapshot.** Editing a source puzzle does not
  change existing items, except through the explicit session "fix position"
  action, which updates both.
- **`sets_for()` / `set_for()`** are the only ownership boundary. A set owned
  by someone else must 404, not 403 (don't reveal its existence).
- **Pagination:** the book puzzle list is 30/page (`?p=N`); the training-set
  list is 50/page (`?p=N`). The training-set page's clone dropdown must list
  **all** the user's sets, not just the current page.
- **Position editing is superuser-only.** The training session's "Fix
  orientation", "Switch side to move", and "Edit position" controls, and the
  puzzle page's rotate / switch-turn forms, are hidden from normal users and
  their POST endpoints return 403/redirect. Do not expose them to players.
- **Django `JSONField` mutation is not tracked** when edited in place. Reassign
  the whole dict/list before saving.
- **python-chess 1.11 gotchas:** use `Score.score()` / `Score.mate()` methods
  (not `.cp` / `.mate`); `Board.push()` does not validate the side to move;
  there is no `chess.rotate_180` — rotate with
  `transform(flip_vertical).transform(flip_horizontal)` (squares map `sq ^ 63`)
  and clear castling rights.
- **OCR orientation:** the tsoj model normalizes diagrams to White's
  perspective. Side-to-move is recovered manually from curated lists and by
  flipping the stored FEN; don't assume OCR got the turn right.
- **`store_page_image` upserts by page number**; `PageImage.save()` generates
  thumbnails on first save. Re-importing a page updates in place.
- **`ALLOWED_HOSTS`/CSRF:** dev allows localhost only. Deploy allows the EC2
  host on `:8888`.

## Frontend / assets

- Base template: `analysis/templates/analysis/base.html`.
- CSS: `analysis/static/analysis/style.css`, `books/static/books/style.css`.
- Interactive boards: `analysis/static/analysis/engine.js` plus vendored
  `books/static/books/vendor/{chessground.min.js,chess.js,stockfish.js}`.
- Stockfish WASM loads from jsDelivr first with the vendored local file as
  offline fallback, through a same-origin glue worker. Search is
  `go depth 25 movetime 1000`, MultiPV 3.
- Do not add a JS framework, bundler, or package manager without explicit
  approval.

## Testing conventions

- Tests live in each app's `tests/` directory; `conftest.py` provides
  authenticated `client`/`owner` fixtures for training and books.
- View tests use Django's test client and assert status codes plus rendered
  context/content. Add a test for every behavior change, especially
  permission/ownership and pagination cases.
- Browser E2E tests live in `tests/e2e/` and are marked `e2e`. They are outside
  `testpaths`, so the default `pytest` run does not collect them; run
  `pytest tests/e2e -m e2e` after installing `requirements-e2e.txt`.
- Avoid tests that need network access, OCR models, or a real engine unless
  marked `integration`.
- Fixtures: `training/tests/conftest.py` creates a non-superuser owner on
  purpose; don't switch it to a superuser or ownership tests stop covering the
  rule.
- CI is defined in `.github/workflows/ci.yml`; deployment is in
  `.github/workflows/deploy.yml`. See `.github/workflows/README.md` for the
  required `EC2_*` secrets and server prerequisites.

## Production notes

Topology (see `deploy/README.md`):

```
browser :8888 → nginx → gunicorn 127.0.0.1:8000 → Django
                         └→ Postgres 16 on 127.0.0.1:5432
```

- Host: Ubuntu EC2 instance; app at `/home/ubuntu/chess-app`; systemd unit
  `chess-app.service`; nginx site `chess-app`.
- Secrets are in `/home/ubuntu/chess-app/.env` (mode 600), not the repo.
- `media/` and `uploaded-books/` were intentionally not deployed; scanned page
  images will 404 until those files are rsynced.
- Redeploy only when asked: install `requirements-deploy.txt`, migrate,
  collectstatic, restart the service.
- After a `loaddata` on Postgres, reset sequences (`sqlsequencereset`); SQLite
  does not need it.

## Agent checklist

1. Read the relevant section of `README.md` / `PROJECT_CONTEXT.md` /
   `design.md` before changing domain logic.
2. Find the existing pattern in the app and follow it; keep the diff focused.
3. Update or add tests in the matching `tests/` directory.
4. Run the narrowest useful test target, then the full fast suite when
   feasible.
5. Update docs if behavior, commands, or routes changed.
6. Report exactly what changed, which commands/tests were run, and any
   limitation (e.g. PDF export untested without GTK).
