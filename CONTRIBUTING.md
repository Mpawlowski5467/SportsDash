# Contributing to SportsDash

SportsDash is a personal project that's open source under the
[MIT license](LICENSE) — bug reports, fixes, and well-scoped features are
welcome. It's opinionated (single user, no accounts, keyless public data
sources only — see the [non-goals in ROADMAP.md](ROADMAP.md)), so for
anything larger than a fix, open an issue first to check the direction.

## Dev setup

Backend (Python 3.12, SQLite by default — zero setup):

```sh
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.txt
uvicorn app.main:app --reload      # http://localhost:8000
```

Frontend (Bun + Vite; the dev server proxies `/api` to `localhost:8000`):

```sh
cd frontend
bun install
bun run dev                        # http://localhost:5173
```

## Before you open a PR

CI gates every PR on all of these — run them locally first:

```sh
# backend (from backend/)
ruff check app tests && ruff format --check app tests
pytest -q                          # ~5s, fully hermetic (no network)

# frontend (from frontend/)
bun run typecheck && bun run lint && bun run test && bun run build
```

CI also reruns the whole backend suite against real Postgres — if your
change touches the database layer, remember SQLite is forgiving where
Postgres is not (FK enforcement, tz-aware datetimes, VARCHAR widths).
You can reproduce that run locally with Docker:

```sh
docker run -d --rm --name pg-test -e POSTGRES_USER=sportsdash \
  -e POSTGRES_PASSWORD=sportsdash -e POSTGRES_DB=sportsdash_test \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
SPORTSDASH_TEST_DATABASE_URL=postgresql+asyncpg://sportsdash:sportsdash@127.0.0.1:55432/sportsdash_test \
  pytest -q
docker stop pg-test
```

## House rules

- **[docs/CONTRACTS.md](docs/CONTRACTS.md) is binding.** `schemas.py` and
  `frontend/src/types.ts` mirror each other; change them together. The
  OpenAPI snapshot test enforces this — after an intentional API change,
  regenerate with `SPORTSDASH_UPDATE_OPENAPI=1 pytest
  tests/test_openapi_snapshot.py` and commit the diff.
- **UTC internally.** Datetimes are stored and compared tz-aware UTC;
  anything read from the DB goes through `timeutil.ensure_utc`.
  Localization happens only at the response boundary or in the frontend.
- **Fictional names only** in tests and sample config — never real teams,
  leagues, or players in fixtures.
- **Parse defensively.** Provider payloads are hostile: missing keys must
  degrade per-record, never crash a refresh. New provider tests use
  recorded JSON fixtures, not live calls (the weekly `live-smoke`
  workflow covers drift).
- **Be a good citizen of the free tiers.** All TheSportsDB access goes
  through `services/tsdb_client.py`'s process-wide pacing gate; don't add
  bypass routes around it.
- **Adding a data source?** Implement the `SportsProvider` protocol —
  the [README's "Adding a provider"](README.md#adding-a-provider) section
  and `app/providers/base.py`'s error contract are the spec.

## Dependency changes

`backend/requirements.txt` holds the human-edited floors; the resolved
pins live in `requirements.lock`. After editing requirements.txt:

```sh
uv pip compile requirements.txt -o requirements.lock --python-version 3.12
```
