#!/bin/zsh
# Recover the complete AgentMemory process pair when the detached iii engine is unhealthy.
set -euo pipefail

CURL_BIN="${WATCHDOG_CURL:-/usr/bin/curl}"
GREP_BIN="${WATCHDOG_GREP:-/usr/bin/grep}"
ID_BIN="${WATCHDOG_ID:-/usr/bin/id}"
DATE_BIN="${WATCHDOG_DATE:-/bin/date}"
SLEEP_BIN="${WATCHDOG_SLEEP:-/bin/sleep}"
LAUNCHCTL_BIN="${WATCHDOG_LAUNCHCTL:-/bin/launchctl}"
LSOF_BIN="${WATCHDOG_LSOF:-/usr/sbin/lsof}"
PS_BIN="${WATCHDOG_PS:-/bin/ps}"
KILL_BIN="${WATCHDOG_KILL:-/bin/kill}"
CMP_BIN="${WATCHDOG_CMP:-/usr/bin/cmp}"

HEALTH_URL="${WATCHDOG_HEALTH_URL:-http://127.0.0.1:3111/agentmemory/health}"
LOG_PATH="${WATCHDOG_LOG:-/Users/zouhao/.agentmemory/logs/watchdog.log}"
SERVICE_PLIST="${WATCHDOG_SERVICE_PLIST:-/Users/zouhao/Library/LaunchAgents/com.agentmemory.server.plist}"
ENGINE_BIN="${WATCHDOG_ENGINE_BIN:-/Users/zouhao/.agentmemory/bin/iii}"
ALT_ENGINE_BIN="${WATCHDOG_ALT_ENGINE_BIN:-/Users/zouhao/.local/bin/iii}"
ENGINE_CONFIG="${WATCHDOG_ENGINE_CONFIG:-/Users/zouhao/.agentmemory/iii-config.yaml}"
TERM_WAIT_ATTEMPTS="${WATCHDOG_TERM_WAIT_ATTEMPTS:-10}"
RECOVERY_STAMP="${WATCHDOG_RECOVERY_STAMP:-/Users/zouhao/.agentmemory/watchdog-recovery.epoch}"
RECOVERY_COOLDOWN_SECONDS="${WATCHDOG_RECOVERY_COOLDOWN_SECONDS:-300}"

# Append one timestamped watchdog event to LOG_PATH.
# Args: $1 is the event message. Returns: the append command status.
# Errors: propagates date or filesystem failures because the script uses errexit.
log_event() {
  print -r -- "$("$DATE_BIN" '+%Y-%m-%dT%H:%M:%S%z') $1" >> "$LOG_PATH"
}

# Probe the configured health endpoint for an AgentMemory service response.
# Args: none. Returns: 0 for a recognized response and nonzero otherwise.
# Errors: curl and grep failures are represented by the nonzero return status.
health_ok() {
  # A valid AgentMemory response proves liveness even when diagnostics return HTTP 503.
  "$CURL_BIN" -sS --max-time 5 "$HEALTH_URL" \
    | "$GREP_BIN" -Eq '"service"[[:space:]]*:[[:space:]]*"agentmemory"'
}

# Compare a candidate engine config with the canonical configured file.
# Args: $1 is the candidate config path. Returns: 0 only for byte-identical files.
# Errors: missing or unreadable files are treated as a non-match.
configs_equivalent() {
  local candidate_config="$1"
  [[ -f "$ENGINE_CONFIG" && -f "$candidate_config" ]] || return 1
  "$CMP_BIN" -s -- "$ENGINE_CONFIG" "$candidate_config"
}

# Validate an iii command without executing any portion of its command line.
# Args: $1 is the command reported by ps. Returns: 0 for an approved binary and
# canonical or byte-equivalent config, and nonzero for every other command.
# Errors: malformed commands and missing configs are treated as unowned.
approved_engine_command() {
  local command="$1" candidate_config
  local -a words
  words=(${(z)command})

  (( ${#words[@]} >= 3 )) || return 1
  [[ "${words[1]}" == "$ENGINE_BIN" || "${words[1]}" == "$ALT_ENGINE_BIN" ]] || return 1
  [[ "${words[2]}" == "--config" ]] || return 1
  candidate_config="${words[3]}"

  [[ "$candidate_config" == "$ENGINE_CONFIG" ]] || configs_equivalent "$candidate_config"
}

# Print only validated AgentMemory engine PIDs and reject unrelated listeners.
# Args: none. Returns: 0 when all listeners are owned, or 2 for a foreign one.
# Errors: a process disappearing during inspection is ignored as already stopped.
validated_engine_pids() {
  local raw_pids pid command
  raw_pids="$("$LSOF_BIN" -nP -t -iTCP:3111 -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "$raw_pids" ]] || return 0

  for pid in ${(f)raw_pids}; do
    [[ "$pid" == <-> ]] || continue
    command="$("$PS_BIN" -p "$pid" -o command= 2>/dev/null || true)"
    if [[ -z "$command" ]]; then
      # The process exited between lsof and ps; it is no longer a target.
      continue
    fi
    if approved_engine_command "$command"; then
      print -r -- "$pid"
      continue
    fi
    log_event "refusing recovery: port 3111 belongs to pid $pid ($command)"
    return 2
  done
}

uid="$("$ID_BIN" -u)"
service="gui/$uid/com.agentmemory.server"

for attempt in 1 2 3; do
  if health_ok; then
    exit 0
  fi
  if (( attempt < 3 )); then
    "$SLEEP_BIN" 2
  fi
done

now_epoch="$("$DATE_BIN" '+%s')"
if [[ -f "$RECOVERY_STAMP" ]]; then
  last_recovery="$(<"$RECOVERY_STAMP")"
  if [[ "$last_recovery" == <-> ]]; then
    recovery_age=$((now_epoch - last_recovery))
    if (( recovery_age >= 0 && recovery_age < RECOVERY_COOLDOWN_SECONDS )); then
      log_event "API unavailable during startup grace (${recovery_age}s/${RECOVERY_COOLDOWN_SECONDS}s); deferring recovery"
      exit 0
    fi
  fi
fi

# Validate ownership before unloading anything. This leaves foreign services untouched.
initial_pids="$(validated_engine_pids)" || exit $?
log_event "API unavailable; recovering worker and detached engine${initial_pids:+ (engine pid(s): ${initial_pids//$'\n'/,})}"

# Prevent KeepAlive from racing a replacement worker against engine cleanup.
"$LAUNCHCTL_BIN" bootout "$service" >/dev/null 2>&1 || true

integer unload_wait=0
while "$LAUNCHCTL_BIN" print "$service" >/dev/null 2>&1; do
  if (( unload_wait >= 10 )); then
    log_event "recovery failed: launchd service did not finish unloading"
    exit 1
  fi
  unload_wait=$((unload_wait + 1))
  "$SLEEP_BIN" 1
done

engine_pids="$(validated_engine_pids)" || exit $?
for pid in ${(f)engine_pids}; do
  "$KILL_BIN" -TERM "$pid" 2>/dev/null || true
done

integer wait_attempt=0
while (( wait_attempt < TERM_WAIT_ATTEMPTS )); do
  any_alive=false
  for pid in ${(f)engine_pids}; do
    if "$KILL_BIN" -0 "$pid" 2>/dev/null; then
      any_alive=true
      break
    fi
  done
  [[ "$any_alive" == false ]] && break
  wait_attempt=$((wait_attempt + 1))
  "$SLEEP_BIN" 1
done

for pid in ${(f)engine_pids}; do
  if "$KILL_BIN" -0 "$pid" 2>/dev/null; then
    log_event "engine pid $pid ignored SIGTERM; sending SIGKILL"
    "$KILL_BIN" -KILL "$pid" 2>/dev/null || true
  fi
done

# Wait for the listener to disappear, then revalidate the boundary before restart.
integer port_wait=0
while (( port_wait < 10 )); do
  remaining_pids="$(validated_engine_pids)" || exit $?
  [[ -z "$remaining_pids" ]] && break
  port_wait=$((port_wait + 1))
  "$SLEEP_BIN" 1
done

remaining_pids="$(validated_engine_pids)" || exit $?
if [[ -n "$remaining_pids" ]]; then
  log_event "recovery failed: validated engine still owns port 3111 (pid(s): ${remaining_pids//$'\n'/,})"
  exit 1
fi

bootstrapped=false
integer bootstrap_attempt=0
while (( bootstrap_attempt < 10 )); do
  if "$LAUNCHCTL_BIN" bootstrap "gui/$uid" "$SERVICE_PLIST" >/dev/null 2>&1; then
    bootstrapped=true
    break
  fi
  bootstrap_attempt=$((bootstrap_attempt + 1))
  "$SLEEP_BIN" 1
done

if [[ "$bootstrapped" != true ]]; then
  log_event "recovery failed: launchd service could not be bootstrapped after 10 attempts"
  exit 1
fi

print -r -- "$("$DATE_BIN" '+%s')" > "$RECOVERY_STAMP"
"$LAUNCHCTL_BIN" kickstart "$service"

log_event "recovery sequence completed"
