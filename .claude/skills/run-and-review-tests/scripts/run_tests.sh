#!/usr/bin/env bash
# Lint + fast test suite always; integration suite only if Docker is reachable.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"

PY=".venv/bin/python"
RUFF=".venv/bin/ruff"
status=0

echo "== ruff check =="
if [ -x "$RUFF" ]; then
  "$RUFF" check . || status=1
else
  echo "$RUFF not found -- skipping lint (run: pip install -e \".[dev]\")" >&2
fi

echo
echo "== fast suite (pytest -m 'not integration') =="
"$PY" -m pytest -m "not integration" || status=1

echo
if docker info >/dev/null 2>&1; then
  echo "== integration suite (pytest -m integration) =="
  "$PY" -m pytest -m integration || status=1
else
  echo "Docker daemon not reachable -- skipping integration suite (needs testcontainers[postgres])." >&2
fi

exit $status
