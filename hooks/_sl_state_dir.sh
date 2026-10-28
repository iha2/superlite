# Shared by hooks: resolve the active superlite run-state directory.
# Caller sets proj; this sets sd to either:
#   $proj/.superlite/sessions/<id>  (when SUPERLITE_SESSION or .superlite/active points there)
#   $proj/.superlite                (legacy flat layout)
# Bash builtins only. Source with: . "$(dirname "$0")/_sl_state_dir.sh"  (or SCRIPT_DIR).

sl_resolve_state_dir() {
  local base="${1:-${proj:-.}}/.superlite" sid=""
  sd="$base"
  if [ -n "${SUPERLITE_SESSION:-}" ] && [ -d "$base/sessions/$SUPERLITE_SESSION" ]; then
    sd="$base/sessions/$SUPERLITE_SESSION"
    return 0
  fi
  if [ -f "$base/active" ]; then
    IFS= read -r sid < "$base/active" || true
    sid="${sid%%[$'\r\n']*}"
    if [ -n "$sid" ] && [ -d "$base/sessions/$sid" ]; then
      sd="$base/sessions/$sid"
      return 0
    fi
  fi
  return 0
}

# True when this state dir (or the flat container) looks like an active run.
sl_run_marker() {
  local d="$1"
  [ -f "$d/stable.json" ] || [ -f "$d/prime.md" ] || [ -f "$d/pool.json" ] || \
    [ -f "$d/orchestrator.json" ] || [ -f "$d/feature.json" ]
}

# True when $1/.superlite has a flat or session orchestrator.json.
sl_has_orchestrator() {
  local base="$1/.superlite" sid=""
  [ -f "$base/orchestrator.json" ] && return 0
  [ -f "$base/active" ] || return 1
  IFS= read -r sid < "$base/active" || true
  sid="${sid%%[$'\r\n']*}"
  [ -n "$sid" ] && [ -f "$base/sessions/$sid/orchestrator.json" ]
}
