# Shared by hooks: resolve the active superlite run-state directory.
# Caller sets proj; this sets sd to either:
#   $proj/.superlite/sessions/<id>  (when SUPERLITE_SESSION, host map, or .superlite/active points there)
#   $proj/.superlite                (legacy flat layout)
# Resolution order matches skills/swarm/scripts/session.py:_active_id:
#   SUPERLITE_SESSION → by_host[SUPERLITE_HOST_SESSION] → active file/registry
# Bash builtins only. Source with: . "$(dirname "$0")/_sl_state_dir.sh"

sl_resolve_state_dir() {
  local base="${1:-${proj:-.}}/.superlite" sid="" host="" body="" re=""
  sd="$base"
  if [ -n "${SUPERLITE_SESSION:-}" ] && [ -d "$base/sessions/$SUPERLITE_SESSION" ]; then
    sd="$base/sessions/$SUPERLITE_SESSION"
    return 0
  fi
  host="${SUPERLITE_HOST_SESSION:-}"
  if [ -n "$host" ] && [ -f "$base/sessions.json" ]; then
    IFS= read -r -d '' body < "$base/sessions.json" || true
    # Match "by_host" then later "<host>": "<sid>" — host ids are alphanumeric/._-
    re='"'${host}'"[[:space:]]*:[[:space:]]*"([A-Za-z0-9][A-Za-z0-9._-]{0,63})"'
    if [[ $body =~ $re ]]; then
      sid="${BASH_REMATCH[1]}"
      if [ -d "$base/sessions/$sid" ]; then
        sd="$base/sessions/$sid"
        return 0
      fi
    fi
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

# True when $1/.superlite has a flat or session orchestrator.json (same selection as sl_resolve_state_dir).
sl_has_orchestrator() {
  local base="$1/.superlite" prev_sd="${sd:-}"
  [ -f "$base/orchestrator.json" ] && return 0
  sl_resolve_state_dir "$1"
  [ -f "$sd/orchestrator.json" ]
  local rc=$?
  sd="$prev_sd"
  return $rc
}
