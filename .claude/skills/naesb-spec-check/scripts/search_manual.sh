#!/usr/bin/env bash
# Full-text search over the real NAESB WGQ 4.0 manual PDF, with context lines.
# Usage: search_manual.sh "<term>" [context_lines]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
PDF="$REPO_ROOT/docs/NAESB-cyber0923-2026-0709.pdf"
TERM="${1:?usage: search_manual.sh \"<term>\" [context_lines]}"
CTX="${2:-3}"

if [ ! -f "$PDF" ]; then
  echo "Manual not found at $PDF" >&2
  exit 1
fi

if ! command -v pdftotext >/dev/null 2>&1; then
  echo "pdftotext not found. Install poppler-utils, e.g.:" >&2
  echo "  brew install poppler        # macOS" >&2
  echo "  apt-get install poppler-utils   # Debian/Ubuntu" >&2
  exit 1
fi

pdftotext -layout "$PDF" - | grep -n -i -C "$CTX" -- "$TERM"
