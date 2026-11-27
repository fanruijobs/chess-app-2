# CI/CD

Two GitHub Actions workflows live here:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to `main`, pull requests, manual | unit tests on Python 3.12 + 3.14, real-Stockfish integration tests, Playwright browser E2E, frontend typecheck/unit tests/build |
| `deploy.yml` | CI succeeds on `main`, manual | builds React, rsyncs source to EC2, installs deps, migrates, collectstatic, installs nginx config, restarts `chess-app`, health-checks Django + `/app/` |

## Required repository secrets

Set these under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `EC2_HOST` | `3.107.245.75` |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | Full contents of the private key (`r-ec2.pem`), including the BEGIN/END lines |

`EC2_SSH_KEY` must be the **private** key, not `.pub`.

The deploy job uses the `production` environment. Add required reviewers under
**Settings → Environments → production** if deployments should need approval.

## Server prerequisites

The deployment job assumes:

- App directory: `/home/ubuntu/chess-app`
- Python venv: `/home/ubuntu/chess-app/.venv`
- Secrets in `/home/ubuntu/chess-app/.env` (mode 600)
- PostgreSQL running on `127.0.0.1:5432`
- nginx listening on `:8888`, gunicorn on `127.0.0.1:8000`
- nginx config installed at `/etc/nginx/sites-available/chess-app`; the deploy job updates it from `deploy/nginx-chess-app.conf` and reloads nginx
- `frontend_dist/app/` served by nginx at `/app/`, API proxy at `/api/`
- `rsync` and `curl` installed
- Passwordless sudo for the restart and nginx reload, e.g.:
  ```sudoers
  ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart chess-app, /bin/systemctl is-active chess-app, /usr/sbin/nginx, /usr/bin/install
  ```
- The EC2 security group allows SSH from GitHub-hosted runners. GitHub runner
  IPs are dynamic; either allow port 22 from the runner ranges (or `0.0.0.0/0`
  as a last resort), or use a self-hosted runner / VPN.

The deploy job preserves the server-side `.env`, `media/`, `uploaded-books/`,
`staticfiles/`, `tmp/`, and `chessdata-fixture.json` via excludes. Do not
remove those excludes.

After `systemctl restart`, the health check polls gunicorn (`:8000`), nginx
(`:8888`), the API CSRF endpoint, and the React app for up to 60 seconds. This
matters because systemd reports the service as active before gunicorn has
finished binding the socket; checking immediately can produce a transient nginx
502.

## React frontend

The React slice lives in `frontend/` and is served at `/app/`:

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173/app/ with /api proxied to Django :8888
npm run typecheck
npm test
npm run build      # writes ../frontend_dist/app
```

The deploy workflow builds the frontend and rsyncs `frontend_dist/` to the
server. `frontend/node_modules/` and `frontend_dist/` are not committed.

## Running tests locally

```bash
# unit tests (integration + e2e excluded by pytest.ini)
.venv/bin/python -m pytest

# real-Stockfish tests
.venv/bin/python -m pytest -m integration

# browser end-to-end tests (first time)
.venv/bin/pip install -r requirements-e2e.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest tests/e2e -m e2e
```

The E2E suite runs against a live Django test server with a transactional test
database; no external services are required.
