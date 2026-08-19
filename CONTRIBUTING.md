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
Postgres is not: it returns **naive** datetimes where asyncpg returns
tz-aware ones, and it ignores VARCHAR widths. (Foreign keys used to be on
that list. They no longer are: `app/db.py` sets `PRAGMA foreign_keys=ON`
for the shipping SQLite engine, matching what `tests/db_engine.py` has
always done — see the FK section in docs/CONTRACTS.md.) You can reproduce
that run locally with Docker:

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
- **Fictional names only** in tests and recorded provider payloads — never
  real teams, leagues, or players in fixtures. Real names *are* fine in the
  ESPN catalog and in whatever the user follows; that's live app data.
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

## Release checklist

Releasing is one `git push` of a tag — the [release
workflow](.github/workflows/release.yml) does the rest. Everything before
that is getting the version right, and the version is the part that bites:
**it is written in five files, and only two of them fail the build when you
forget.**

### The version lives here

| File | Enforced by | Notes |
|---|---|---|
| `backend/app/version.py` → `APP_VERSION` | — | **The source of truth.** `app/routes/meta.py` re-exports it under the same name, so `main.py` still passes it to `FastAPI(version=…)` and `GET /api/meta` still returns it; `app/useragent.py` stamps it into every outbound request. It sits in a leaf module precisely so a provider can read the version without importing the route package. Edit this one first. |
| `frontend/src-tauri/tauri.conf.json` → `version` | `tests/test_api.py::test_app_version_has_one_source_of_truth` | What the desktop bundle ships as, and what names the artifact: `SportsDash_<version>_aarch64.dmg`. |
| `backend/tests/openapi_snapshot.json` → `info.version` | `tests/test_openapi_snapshot.py` | **Regenerated, never hand-edited** — see below. |
| `frontend/package.json` → `version` | *nothing* | Drifts silently. |
| `frontend/src-tauri/Cargo.toml` → `version` | *nothing* | Drifts silently. Tauri takes the version from `tauri.conf.json`, so this one is cosmetic — but it is the version the Rust crate reports, so keep it honest. |

Two of those failures are worth knowing in advance, because neither message
names the file you actually have to edit:

- The version-pin test fails as a bare string comparison of the old and new
  values. It has read `frontend/src-tauri/tauri.conf.json`, but the path is
  built a few lines above the assertion, so the output does not say so.
- The **OpenAPI snapshot test fails too**, because the schema embeds
  `info.version` — and its message talks about a changed API schema and
  mirroring fields into `frontend/src/types.ts`, which for a version bump is
  a red herring. No API shape changed; just regenerate the snapshot:

  ```sh
  cd backend && SPORTSDASH_UPDATE_OPENAPI=1 pytest tests/test_openapi_snapshot.py
  ```

  and commit the one-line diff.

### The sequence

1. **Land the work on `main` and confirm CI is green there.** CI triggers on
   pushes to `main` and PRs into it — **not on tags**. Pushing `v1.4.0` runs
   the release build and nothing else, so an untested commit will happily
   become a `.dmg`. Check the run for the exact commit you are about to tag.
2. **Bump the version** in all five files above, then regenerate the OpenAPI
   snapshot.
3. **Run the gates locally** (the same ones in *Before you open a PR*):

   ```sh
   cd backend  && ruff check app tests desktop_server.py \
               && ruff format --check app tests desktop_server.py && pytest -q
   cd frontend && bun run typecheck && bun run lint && bun run test && bun run build
   ```

4. **Optional but cheap — dry-run the build.** Run the release workflow
   manually (`gh workflow run "Release desktop app"`). A `workflow_dispatch`
   run builds the `.dmg` and uploads it as a plain workflow artifact
   (`SportsDash-dmg`) *without* creating a release, so a build that only
   breaks on the macOS runner is caught before a tag exists. Locally,
   `./scripts/build-desktop.sh` does the same thing on your own machine.
5. **Commit, push to `main`, wait for green CI.**
6. **Tag and push:**

   ```sh
   git tag -a v1.4.0 -m "SportsDash 1.4.0"
   git push origin v1.4.0
   ```

   Do **not** create the GitHub Release by hand first — the workflow creates
   it for the tag (`softprops/action-gh-release` with
   `generate_release_notes: true`) and attaches the `.dmg` it just built.
7. **Verify the release**: the run is green, the release page exists for the
   tag, and the attached asset is named for the version you bumped — if it
   still says the old number, `tauri.conf.json` was missed and the pin test
   was never run against the tagged commit.

Releases ship **unsigned** unless the six `APPLE_*` repository secrets exist;
the workflow passes them through as empty strings and `build-desktop.sh`
drops the empty ones, so nothing needs changing either way. See *Code
signing / Gatekeeper* in [docs/desktop.md](docs/desktop.md).
