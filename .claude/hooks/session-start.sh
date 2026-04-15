#!/bin/bash
set -euo pipefail

cd "$CLAUDE_PROJECT_DIR"
git fetch origin main --quiet
git merge origin/main --ff-only --quiet 2>/dev/null || true

WIKI_DIR="$CLAUDE_PROJECT_DIR/graphify-out/pipka-wiki"
if [ -d "$WIKI_DIR" ]; then
  echo "[session-start] Knowledge graph loaded:"
  find "$WIKI_DIR" -name "*.md" | sed 's/^/  - /'
  echo "[session-start] Entry point: graphify-out/pipka-wiki/index.md"
else
  echo "[session-start] WARNING: graphify-out/pipka-wiki/ not found"
fi
