#!/usr/bin/env bash
#
# Prove the newest backup actually restores — a backup that has never been
# restored is a hope, not a backup:
#   1. Pick the newest backups/sportsdash-*.sql.gz.
#   2. Freshness check: warn if it is older than
#      SPORTSDASH_BACKUP_MAX_AGE_DAYS (default 16) days — a stale newest
#      dump means the daily backup loop is dead.  `--strict-age` makes
#      staleness fatal (for homelab cron, where the loop should be alive).
#   3. Restore into a throwaway postgres:16-alpine container.  No host
#      ports are published — everything goes through `docker exec`, so a
#      stale container shadowing a port can never fool the drill.
#   4. Sanity-check row counts (teams, leagues, games, standings_archive,
#      players) and, when games exist, that MAX(start_time) is real.
#
# The container is removed on exit; any failed step exits nonzero.
#
# Usage: scripts/verify-backup.sh [--strict-age]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is required but not installed. $2" >&2
    exit 1
  }
}
need docker "Install Docker Desktop or OrbStack"
need gzip "gzip ships with macOS and every Linux distro"

STRICT_AGE=0
for arg in "$@"; do
  case "$arg" in
    --strict-age) STRICT_AGE=1 ;;
    -h | --help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "error: unknown argument '$arg' (only --strict-age is supported)" >&2
      exit 2
      ;;
  esac
done

docker info >/dev/null 2>&1 || {
  echo "error: docker daemon is not running — start Docker Desktop / OrbStack first" >&2
  exit 1
}

# --- 1. Newest dump ---------------------------------------------------------
newest="$(ls -t "$ROOT"/backups/sportsdash-*.sql.gz 2>/dev/null | head -1 || true)"
if [ -z "$newest" ]; then
  echo "error: no dumps matching backups/sportsdash-*.sql.gz — has the compose 'backup' service ever run?" >&2
  exit 1
fi

# --- 2. Freshness -----------------------------------------------------------
# BSD stat (macOS) first, GNU stat as the fallback.
mtime="$(stat -f %m "$newest" 2>/dev/null || stat -c %Y "$newest" 2>/dev/null)" || {
  echo "error: cannot read the modification time of $newest" >&2
  exit 1
}
age_days=$((($(date +%s) - mtime) / 86400))
max_age="${SPORTSDASH_BACKUP_MAX_AGE_DAYS:-16}"
echo "==> Newest dump: $newest (${age_days}d old)"
if [ "$age_days" -gt "$max_age" ]; then
  if [ "$STRICT_AGE" -eq 1 ]; then
    echo "error: newest dump is ${age_days} days old (max ${max_age}) — the daily backup loop looks dead" >&2
    exit 1
  fi
  echo "WARNING: newest dump is ${age_days} days old (max ${max_age}) — the backup loop only runs while compose is up" >&2
fi

# --- 3. Throwaway restore target -------------------------------------------
# Fresh empty database under the sportsdash role: the dumps have no
# --clean/--create and carry ALTER TABLE ... OWNER TO sportsdash lines.
CONTAINER="sportsdash-restore-verify-$$"
cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Starting throwaway container $CONTAINER (postgres:16-alpine, no host ports)…"
docker run --rm -d --name "$CONTAINER" \
  -e POSTGRES_USER=sportsdash \
  -e POSTGRES_PASSWORD=sportsdash \
  -e POSTGRES_DB=sportsdash \
  postgres:16-alpine >/dev/null || {
  echo "error: could not start the postgres:16-alpine container" >&2
  exit 1
}

# The entrypoint boots a socket-only temp server during init; asking for TCP
# means we only proceed once the *real* server is up.
deadline=$((SECONDS + 60))
until docker exec "$CONTAINER" pg_isready -q -h 127.0.0.1 -U sportsdash 2>/dev/null; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "error: postgres in $CONTAINER not ready after 60s (see 'docker logs $CONTAINER')" >&2
    exit 1
  fi
  sleep 1
done

# --- 4. Restore -------------------------------------------------------------
# ON_ERROR_STOP is load-bearing: without it psql shrugs past mid-file errors
# and exits 0.  The script-level pipefail catches a gzip/dump-read failure
# for the same reason the compose backup comment gives.  The dump embeds
# pg_dump 16's \restrict meta-commands, so psql inside the 16-alpine
# container is exactly the right client.  stdout goes to /dev/null because
# -q silences command tags but not result rows (the dump's set_config
# SELECT would leak through); errors still reach stderr and the exit code.
echo "==> Restoring $(basename "$newest")…"
if ! gzip -dc "$newest" | docker exec -i "$CONTAINER" psql -q -v ON_ERROR_STOP=1 -U sportsdash sportsdash >/dev/null; then
  echo "error: restore failed — psql aborted mid-dump (ON_ERROR_STOP) or the dump could not be decompressed" >&2
  exit 1
fi

# --- 5. Sanity checks -------------------------------------------------------
q() {
  docker exec "$CONTAINER" psql -tA -v ON_ERROR_STOP=1 -U sportsdash sportsdash -c "$1"
}

count() {
  q "SELECT count(*) FROM public.$1" || {
    echo "error: counting public.$1 failed — table missing from the restored dump?" >&2
    exit 1
  }
}

teams="$(count teams)"
leagues="$(count leagues)"
games="$(count games)"
standings_archive="$(count standings_archive)" # may be 0; the table must exist
players="$(count players)"

[ "$teams" -gt 0 ] || {
  echo "error: restored database has 0 teams — dump is empty or truncated" >&2
  exit 1
}
[ "$leagues" -gt 0 ] || {
  echo "error: restored database has 0 leagues — dump is empty or truncated" >&2
  exit 1
}
if [ "$games" -gt 0 ]; then
  has_start="$(q "SELECT (MAX(start_time) IS NOT NULL)::int FROM public.games")"
  [ "$has_start" = "1" ] || {
    echo "error: games rows exist but MAX(start_time) is NULL — data looks mangled" >&2
    exit 1
  }
fi

echo "==> Restored row counts:"
printf '    %-20s %8s\n' table rows
printf '    %-20s %8s\n' teams "$teams"
printf '    %-20s %8s\n' leagues "$leagues"
printf '    %-20s %8s\n' games "$games"
printf '    %-20s %8s\n' standings_archive "$standings_archive"
printf '    %-20s %8s\n' players "$players"
echo "==> OK: $(basename "$newest") restores cleanly."
