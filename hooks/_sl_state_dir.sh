# Shared by hooks: resolve the active superlite run-state directory.
# Caller sets proj; this sets sd to either:
#   $proj/.superlite/sessions/<id>  (when SUPERLITE_SESSION, host map, or .superlite/active points there)
#   $proj/.superlite                (legacy flat layout)
# Resolution order matches skills/swarm/scripts/session.py:_active_id:
#   SUPERLITE_SESSION → by_host[host] → active file/registry
# Host comes from SUPERLITE_HOST_SESSION, else common payload keys on $input
# (session_id / conversation_id / sessionId) when the caller left $input set.
# Bash builtins only. Source with: . "$(dirname "$0")/_sl_state_dir.sh"

# Escape a string for use inside a bash ERE (so host "chat.a" does not match "chat-a").
sl_re_escape() {
  local s="$1" out="" i c
  for ((i = 0; i < ${#s}; i++)); do
    c="${s:i:1}"
    case "$c" in
      \\|\[|\]|\^|\$|\.|\*|\+|\?|\(|\)|\{|\}|\|) out+="\\$c" ;;
      *) out+="$c" ;;
    esac
  done
  REPLY="$out"
}

# If SUPERLITE_HOST_SESSION is unset, copy a native chat id from the hook payload ($input).
sl_bind_host_from_payload() {
  [ -n "${SUPERLITE_HOST_SESSION:-}" ] && return 0
  [ -n "${input:-}" ] || return 0
  local key re body
  for key in session_id conversation_id sessionId conversationId; do
    re='"'"$key"'"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)"'
    if [[ $input =~ $re ]]; then
      body="${BASH_REMATCH[1]}"
      body="${body//\\\"/\"}"
      body="${body%%[$'\r\n']*}"
      if [ -n "$body" ]; then
        SUPERLITE_HOST_SESSION="$body"
        export SUPERLITE_HOST_SESSION
        return 0
      fi
    fi
  done
  return 0
}

# Exact by_host lookup: find "host": "sid" only inside the by_host object.
sl_host_session_id() {
  local base="$1" host="$2" body="" bh="" esc="" re=""
  REPLY=""
  [ -n "$host" ] && [ -f "$base/sessions.json" ] || return 1
  IFS= read -r -d '' body < "$base/sessions.json" || true
  # Narrow to the by_host object when present (avoids matching other keys).
  if [[ $body =~ \"by_host\"[[:space:]]*:[[:space:]]*\{([^\{\}]*)\} ]]; then
    bh="${BASH_REMATCH[1]}"
  else
    bh="$body"
  fi
  sl_re_escape "$host"
  esc="$REPLY"
  re='"'${esc}'"[[:space:]]*:[[:space:]]*"([A-Za-z0-9][A-Za-z0-9._-]{0,63})"'
  if [[ $bh =~ $re ]]; then
    REPLY="${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

sl_resolve_state_dir() {
  local base="${1:-${proj:-.}}/.superlite" sid="" host=""
  sd="$base"
  sl_bind_host_from_payload
  if [ -n "${SUPERLITE_SESSION:-}" ] && [ -d "$base/sessions/$SUPERLITE_SESSION" ]; then
    sd="$base/sessions/$SUPERLITE_SESSION"
    return 0
  fi
  host="${SUPERLITE_HOST_SESSION:-}"
  if sl_host_session_id "$base" "$host"; then
    sid="$REPLY"
    if [ -d "$base/sessions/$sid" ]; then
      sd="$base/sessions/$sid"
      return 0
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
