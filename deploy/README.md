# Deployment

Target: a single EC2 box (Ubuntu 24.04) at `3.107.245.75`, serving on **port 8888**.

```
browser :8888 ──> nginx ──┬── /static/  → /home/ubuntu/chess-app/staticfiles/
                          └── /         → gunicorn 127.0.0.1:8000 → Django
                                                                    │
Postgres 16 (127.0.0.1:5432, scram-sha-256, role+db `chessapp`) <───┘
```

## Files here

| File | Installed to |
| --- | --- |
| `chess-app.service` | `/etc/systemd/system/chess-app.service` |
| `nginx-chess-app.conf` | `/etc/nginx/sites-available/chess-app` (symlinked into `sites-enabled/`) |

## Two things that are easy to get wrong

**1. The security group must allow TCP 8888.** Until it does, the port times out
from the internet even though everything on the box looks healthy. From inside
the instance `curl http://127.0.0.1:8888/` works regardless — that only proves
nginx is up, not that it is reachable.

**2. `/home/ubuntu` must be traversable by `www-data`.** It ships as mode `750`,
so nginx cannot reach the app directory and every `/static/` request returns
**403** with `open() ... failed (13: Permission denied)` in
`/var/log/nginx/error.log`. Fix with:

```bash
sudo chmod o+x /home/ubuntu   # traverse only — the directory still can't be listed
```

`o+x` without `o+r` lets nginx walk into known paths without being able to list
the directory, and `.env` stays mode `600` so it remains unreadable.

## Settings

`config/settings_deploy.py` imports the dev settings and overrides only what
must differ: `DEBUG=False`, the Postgres database, `STATIC_ROOT`, the Linux
`STOCKFISH_PATH`, and `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` for `:8888`.
Select it with `DJANGO_SETTINGS_MODULE=config.settings_deploy`.

Secrets live in `/home/ubuntu/chess-app/.env` (mode 600, not in the repo).
The deploy settings read `POSTGRES_PASSWORD` and friends from there.

## Redeploying

```bash
# 1. From the dev machine — tracked + uncommitted files, nothing gitignored.
COPYFILE_DISABLE=1 git ls-files -co --exclude-standard -z \
  | COPYFILE_DISABLE=1 tar --null -T - -czf /tmp/chess-app-src.tgz
scp -i ~/Downloads/r-ec2.pem /tmp/chess-app-src.tgz ubuntu@3.107.245.75:/tmp/

# 2. On the instance.
cd /home/ubuntu/chess-app
tar xzf /tmp/chess-app-src.tgz          # .env and chessdata-fixture.json are preserved
.venv/bin/pip install -r requirements-deploy.txt
DJANGO_SETTINGS_MODULE=config.settings_deploy .venv/bin/python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings_deploy .venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart chess-app
```

`COPYFILE_DISABLE=1` matters: without it macOS `bsdtar` embeds `._*` AppleDouble
resource-fork members that GNU tar then unpacks as thousands of junk files.

## Refreshing data from a local SQLite database

```bash
# Local. Exclude the tables migrations rebuild, and sessions/log entries.
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -e sessions -e admin.logentry \
  -o /tmp/chessdata.json
scp -i ~/Downloads/r-ec2.pem /tmp/chessdata.json ubuntu@3.107.245.75:/tmp/

# Remote. loaddata does NOT advance Postgres sequences — reset them or the
# next INSERT collides on the primary key.
cd /home/ubuntu/chess-app
.venv/bin/python manage.py flush --noinput    # destructive: wipes current rows
.venv/bin/python manage.py loaddata /tmp/chessdata.json
.venv/bin/python manage.py shell -c "
from django.core.management import call_command
from django.db import connection
sql = call_command('sqlsequencereset', 'auth','chessdata','books','training','analysis')
with connection.cursor() as c: c.execute(sql)"
```

## Pulling training data back from the instance

Training marks are made on the box, so deploy is the source of truth for
them; the local SQLite copy can be refreshed without touching the other
apps. The two databases do **not** share `auth.User` PKs (fanrui89 is 1 on
the instance, 2 locally), so dump with natural foreign keys or every set
lands on the wrong owner:

```bash
# On the instance.
cd /home/ubuntu/chess-app
DJANGO_SETTINGS_MODULE=config.settings_deploy .venv/bin/python manage.py \
  dumpdata --natural-foreign --indent 1 training -o /tmp/training_sync.json
# scp it down; locally, back up db.sqlite3 first, delete all training rows
# (TrainingSet / TrainingSetItem / TrainingAttempt), then:
.venv/bin/python manage.py loaddata /tmp/training_sync.json
```

Only `owner` needs a natural key — `books.Puzzle` / `analysis.TurningPoint`
have none, so they stay PKs, and those PKs do align between the two
databases. The JSON fixture truncates datetimes to milliseconds (harmless);
SQLite needs no sequence reset afterwards. `Puzzle.attempts/solved/failed`
counters are bumped by marking and are **not** part of this dump.

## Not deployed

`media/` (4.8 GB of scanned book pages) and `uploaded-books/` are gitignored and
were deliberately not copied, so the 1,828 `books.PageImage` rows point at files
that are not present — page images will 404 in the books app. Everything else
(games, puzzles, training sets, analysis) is intact. To populate later:

```bash
rsync -avz --progress -e "ssh -i ~/Downloads/r-ec2.pem" media/ ubuntu@3.107.245.75:/home/ubuntu/chess-app/media/
```
