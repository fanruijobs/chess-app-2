# CI/CD

Two GitHub Actions workflows live here:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | push to `main`, pull requests, manual | unit tests on Python 3.12 + 3.14, real-Stockfish integration tests, Playwright browser E2E |
| `deploy.yml` | CI succeeds on `main`, manual | rsync source to the EC2 box, install deps, migrate, collectstatic, restart `chess-app`, health-check |

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
- `rsync` and `curl` installed
- Passwordless sudo for the restart, e.g.:
  ```sudoers
  ubuntu ALL=(root) NOPASSWD: /bin/systemctl restart chess-app, /bin/systemctl is-active chess-app
  ```
- The EC2 security group allows SSH from GitHub-hosted runners. GitHub runner
  IPs are dynamic; either allow port 22 from the runner ranges (or `0.0.0.0/0`
  as a last resort), or use a self-hosted runner / VPN.

`rsync --delete` preserves the server-side `.env`, `media/`, `uploaded-books/`,
`staticfiles/`, `tmp/`, and `chessdata-fixture.json` via excludes. Do not
remove those excludes.

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
