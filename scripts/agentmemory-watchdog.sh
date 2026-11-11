#!/bin/zsh
# Recover an AgentMemory v0.9.31+ Worker Compose instance when its REST API stops answering.
#
# Ownership and process signalling are delegated to the official `agentmemory stop`
# command (never with --force): it validates the saved Compose identity and refuses
# to signal foreign port holders. This script only probes, rate-limits, and restarts
# the launchd job that runs the official CLI.
set -euo pipefail

CURL_BIN="${WATCHDOG_CURL:-/usr/bin/curl}"
GREP_BIN="${WATCHDOG_GREP:-/usr/bin/grep}"
ID_BIN="${WATCHDOG_ID:-/usr/bin/id}"
DATE_BIN="${WATCHDOG_DATE:-/bin/date}"
SLEEP_BIN="${WATCHDOG_SLEEP:-/bin/sleep}"
LAUNCHCTL_BIN="${WATCHDOG_LAUNCHCTL:-/bin/launchctl}"
AGENTMEMORY_BIN="${WATCHDOG_AGENTMEMORY:-/opt/homebrew/bin/agentmemory}"

# /livez is unauthenticated since v0.9.30; /health requires the daemon secret.
HEALTH_URL="${WATCHDOG_HEALTH_URL:-http://127.0.0.1:3111/agentmemory/livez}"
LOG_PATH="${WATCHDOG_LOG:-/Users/zouhao/.agentmemory/logs/watchdog.log}"
SERVICE_PLIST="${WATCHDOG_SERVICE_PLIST:-/Users/zouhao/Library/LaunchAgents/com.agentmemory.server.plist}"
DATA_DIR="${WATCHDOG_DATA_DIR:-/Users/zouhao/data}"
RECOVERY_STAMP="${WATCHDOG_RECOVERY_STAMP:-/Users/zouhao/.agentmemory/watchdog-recovery.epoch}"
RECOVERY_COOLDOWN_SECONDS="${WATCHDOG_RECOVERY_COOLDOWN_SECONDS:-300}"

# The CLI inspects Compose processes with lsof/ps from /usr/sbin and /bin.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export AGENTMEMORY_DATA_DIR="$DATA_DIR"

# Append one timestamped watchdog event to LOG_PATH.
# Args: $1 is the event message. Returns: the append command status.
log_event() {
  print -r -- "$("$DATE_BIN" '+%Y-%m-%dT%H:%M:%S%z') $1" >> "$LOG_PATH"
}

# Probe the liveness endpoint for an AgentMemory service response.
# Returns: 0 for a recognized response and nonzero otherwise.
health_ok() {
  "$CURL_BIN" -sS --max-time 5 "$HEALTH_URL" \
    | "$GREP_BIN" -Eq '"service"[[:space:]]*:[[:space:]]*"agentmemory"'
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

# Record the attempt before acting so a failing recovery is not retried every minute.
print -r -- "$now_epoch" > "$RECOVERY_STAMP"
log_event "API unavailable; stopping managed Compose instance via agentmemory stop"

if ! stop_output="$("$AGENTMEMORY_BIN" stop 2>&1)"; then
  log_event "recovery refused: agentmemory stop failed; leaving processes untouched: ${${stop_output//$'\n'/ }[1,400]}"
  exit 1
fi

if "$LAUNCHCTL_BIN" print "$service" >/dev/null 2>&1; then
  # The job is loaded but idle (the CLI exits after starting Compose); rerun it.
  if ! "$LAUNCHCTL_BIN" kickstart -k "$service" >/dev/null 2>&1; then
    log_event "recovery failed: launchd kickstart of $service failed"
    exit 1
  fi
elif ! "$LAUNCHCTL_BIN" bootstrap "gui/$uid" "$SERVICE_PLIST" >/dev/null 2>&1; then
  # RunAtLoad starts the CLI as part of a successful bootstrap.
  log_event "recovery failed: launchd service could not be bootstrapped"
  exit 1
fi

log_event "recovery sequence completed"
