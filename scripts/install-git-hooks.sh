#!/usr/bin/env bash
# Point this clone at .githooks (commit-msg strips Cursor agent trailers).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
git config core.hooksPath .githooks
chmod +x .githooks/commit-msg 2>/dev/null || true
echo "core.hooksPath=$(git config core.hooksPath) (Cursor Co-authored-by lines will be stripped on commit)"
