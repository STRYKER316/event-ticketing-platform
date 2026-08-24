#!/bin/sh
# Assembles a self-contained Elastic Beanstalk deployment bundle.
#
# infra/docker-compose.yml's event-service/booking-service/payment-service/
# frontend build contexts point one level above infra/ ("../services",
# "../frontend") -- correct for local dev (`cd infra && docker compose up`,
# where Compose resolves build.context relative to the compose file's own
# location, not the CWD), but not deployable as-is: EB's Docker-Compose
# platform expects docker-compose.yml at the deployment bundle's root with
# every build context nested underneath it (confirmed against AWS's own
# docker-compose quickstart, which only ever uses "./subdir" contexts,
# never "../subdir" -- P10.T1 pre-flight check). This copies services/,
# frontend/, and infra/'s own subdirectories into a flat bundle and
# rewrites the two out-of-bundle context paths to match, without touching
# the canonical infra/docker-compose.yml used for local dev.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:?usage: build-eb-bundle.sh <output-dir>}"

case "$OUT_DIR" in
  /|"$REPO_ROOT") echo "refusing to rm -rf $OUT_DIR" >&2; exit 1 ;;
esac
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# Same noise .gitignore already excludes from version control, plus .env
# itself (frontend/README.md's own npm-run-dev instructions create
# frontend/.env locally -- harmless in the bundle since Vite's build-arg env
# always wins, but it has no reason to ship). .env.example is kept, same as
# .gitignore's own !.env.example negation -- the --include must come before
# the broader --exclude=.env.* for rsync to honor it.
EXCLUDES="--exclude=.venv --exclude=__pycache__ --exclude=*.pyc --exclude=.pytest_cache --exclude=.mypy_cache --exclude=.ruff_cache --exclude=*.egg-info --exclude=node_modules --exclude=dist --exclude=build --exclude=.env --include=.env.example --exclude=.env.*"

# shellcheck disable=SC2086
rsync -a $EXCLUDES "$REPO_ROOT/services/" "$OUT_DIR/services/"
# shellcheck disable=SC2086
rsync -a $EXCLUDES "$REPO_ROOT/frontend/" "$OUT_DIR/frontend/"

for d in keycloak postgres docker-socket-proxy prometheus grafana .platform; do
  if [ -d "$REPO_ROOT/infra/$d" ]; then
    # shellcheck disable=SC2086
    rsync -a $EXCLUDES "$REPO_ROOT/infra/$d/" "$OUT_DIR/$d/"
  fi
done

sed -e 's#context: \.\./services#context: ./services#' \
    -e 's#context: \.\./frontend#context: ./frontend#' \
    "$REPO_ROOT/infra/docker-compose.yml" > "$OUT_DIR/docker-compose.yml"

echo "EB deployment bundle assembled at: $OUT_DIR"
