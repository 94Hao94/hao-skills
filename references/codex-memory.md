# Codex EP retrieval and agentmemory capture

Codex retrieves preferences, history and evidence through the one EP MCP supplied
by the EP installer. Claude keeps its existing agentmemory plugin and configuration.
This document covers the independent Codex writer that replaces the full
agentmemory plugin. It does not set `AGENTMEMORY_INJECT_CONTEXT`, edit shared
`~/.agentmemory/.env`, remove Claude skills, or modify original shared skill files.

## Adapter interface

The adapter needs Python 3.10+ and only the Python standard library. Deploy
`scripts/codex_agentmemory_capture.py` to a durable installed skill location before
removing `agentmemory@agentmemory` from Codex. All paths passed to Codex must be
absolute. Hook commands accept one JSON object on stdin and produce **no context
on stdout**:

```text
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook SessionStart
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook UserPromptSubmit
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook PostToolUse
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook PostToolUseFailure
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook PreCompact
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook Stop
python3 /absolute/skill/scripts/codex_agentmemory_capture.py hook SessionEnd
```

The installed configuration registers only those seven events. The adapter also
understands SubagentStart, SubagentStop, TaskCompleted and Notification for a host
that explicitly supports them; these extra events are not installed by default. `Stop` captures a turn boundary; only `SessionEnd`
marks the server session completed. Missing session identity, SDK child events,
unknown events and retrieval/enrichment hooks do not produce observations.
Lifecycle events capture metadata, not transcript contents. Tool outputs are
bounded by `AGENTMEMORY_CAPTURE_OUTPUT_MAX` (8000 characters by default).

The standalone MCP command is `python3 /absolute/skill/scripts/codex_agentmemory_capture.py mcp`.
Its hard whitelist exposes only `memory_save` and `memory_lesson_save`; supplying
an arbitrary tool name or endpoint cannot reach another daemon function. The
newline-delimited JSON-RPC implementation supports initialize, initialized
notifications, ping, tools/list and tools/call. A queued request reports
`saved: false`, rather than claiming a successful write. An explicit save can
be delivered by a subsequent retry after the caller sees a pending response;
inspect the returned event ID instead of blindly repeating a lesson save.

## Actual write protocol

| POST endpoint | Use |
| --- | --- |
| `/agentmemory/observe` | Prompt, tool and lifecycle observation; implicitly creates sessions from project and cwd |
| `/agentmemory/session/end` | Marks a final SessionEnd completed after its observation |
| `/agentmemory/remember` | Explicit memory_save; normalizes concepts/files to string arrays |
| `/agentmemory/lessons` | Explicit memory_lesson_save |

The installed daemon's `/agentmemory/session/start` always invokes
`mem::context`. The adapter deliberately uses a session_start observation instead;
it never calls `/session/start`, `/context`, `/enrich` or any search endpoint.
The current observe API whitelists top-level fields. Source agent, agent ID, model
and session identity are therefore preserved in `data.capture_metadata`; this
works even on versions that drop a top-level agentId/model field. It does not
change the daemon's own agent-isolation policy.

Authentication follows the existing client resolver: explicit
`AGENTMEMORY_SECRET`, then (only for loopback URLs) `~/.agentmemory/.env`, then
`~/.agentmemory/secret`. `AGENTMEMORY_CREDENTIAL_MODE=explicit` disables local-file
fallback. Shell-style placeholder values are ignored. Credentials never appear
in status output, queue rows or error messages. Redirects are refused, preventing
an authenticated daemon request from following a redirect to a second host.

## Durable capture and retry

The default private SQLite queue is
`~/.codex/agentmemory-capture/queue.sqlite3`; override it with
`CODEX_AGENTMEMORY_QUEUE` or `--queue /absolute/path`. `AGENTMEMORY_URL` or `--url`
selects the daemon (default `http://localhost:3111`). Global options precede the
subcommand. SQLite FULL synchronization commits a scrubbed event before network
I/O, and transactions serialize concurrent drainers. Credentials and private
blocks are scrubbed before both disk and network use. The database uses mode 0600.

Host event IDs determine a stable local deduplication key. Without an ID or host
timestamp, each invocation gets a fresh identity so identical legitimate prompts
are not collapsed. Delivered rows retain only their event hash; private body data
is released. Failed and rejected bodies remain available for diagnosis. A 2xx
reply carrying bootId/durableAfterMs retains the event until a later confirmation
from the same daemon boot after its durability deadline. A changed boot replays
the unchanged event. Older servers without this marker use the write response
as their acknowledgement. This is at-least-once delivery across ambiguous network
failures; exactly-once explicit memory/lesson writes require daemon support.

Each subsequent captured event or MCP write opportunistically drains the queue.
After idle downtime, explicitly drain it:

```text
python3 /absolute/skill/scripts/codex_agentmemory_capture.py status
python3 /absolute/skill/scripts/codex_agentmemory_capture.py flush
```

`status` prints state counts, never payloads. `flush` has a 20-second total budget;
normal hook calls have a 2.5-second drain budget with one-second request timeouts.
Network/5xx/408/429 failures back off up to five minutes. Other 4xx records remain
`rejected` and are not silently retried. After correcting a permanent rejection,
inspect the retained queue locally before deciding whether to replay it.

`CODEX_AGENTMEMORY_CAPTURE=0|false|off` (or `AGENTMEMORY_CAPTURE` with those values)
disables hook capture and MCP writes. `AGENTMEMORY_SDK_CHILD=1` also opts out.
`AGENTMEMORY_CAPTURE_ALLOW` / `AGENTMEMORY_CAPTURE_DENY` accept comma/space-separated
globs. Memory-tool and EP-tool observations always remain excluded, even when the
allowlist is `*`; the adapter does not capture memory retrieval output recursively.

## Configuration helper and rollback

`configure_codex_memory.py` defaults to a dry run. It preserves unrelated TOML
sections and existing skill overrides, removes only named legacy MCP server
sections, and registers `agentmemory_capture` with an explicit two-tool `enabled_tools`
allowlist. An existing `[plugins."agentmemory@agentmemory"]` entry is disabled in
Codex only, so a later merge cannot restore it accidentally. Optional `--ep-config` accepts a
JSON object `{ "name": "evolving-profile", "command": "/absolute/python", "args":
["/absolute/ep-mcp.py"] }`; optional `env` contains non-secret values. Include
additional old EP aliases with repeatable `--legacy-server`. Do not put credentials
in this JSON or command-line arguments.

`--merge-hooks` removes old agentmemory hook commands from **Codex's** hooks JSON
and adds this independent adapter, preserving unrelated commands including the
universal skill gate. The main installer should merge the gate first. `--project`
may be repeated to include actual project `.agents/skills` copies. Legacy retrieval
skills and agentmemory guidance are disabled using `[[skills.config]]` **folder**
paths, with both lexical symlink and canonical paths. Existing obsolete SKILL.md
file overrides are also disabled. Shared symlinks and source skills remain intact.
The current setup-dev-spec-memory skill and write-oriented remember/lesson skills
are not disabled.

Only `--apply` mutates files. It creates private timestamped backups next to each
changed file and atomically replaces it. Full-plugin removal must use the Codex
plugin-management tool after the adapter is durable; this helper does not uninstall
plugins or touch Claude. Rollback restores the saved Codex files, restores the
previous plugin connection if needed, and leaves the queue/new memory data intact.
On Python 3.11+ the helper validates TOML with tomllib. Always run the actual Codex
configuration parser (`codex mcp list`) after applying, and refresh the host MCP
catalog before claiming native-tool validation. A standalone stdio handshake does
not prove that the current chat's native tool catalog has refreshed.

## Verification

The focused suite uses an ephemeral loopback mock server and temporary queues;
it never writes synthetic observations to the production daemon:

```text
python3 -m unittest tests.test_codex_agentmemory_capture tests.test_configure_codex_memory -v
```

Checks cover lifecycle write endpoints, no-context session creation, metadata,
redaction, capture deny rules, local credential resolution boundaries, durable
retry across process restart, concurrency and local deduplication, daemon boot
changes, retained rejections, MCP whitelist, stdio handshake, opt-out, source-skill
preservation, existing TOML/skill override retention, private backups and idempotent
hook merges. Production verification should use a naturally occurring task after
installation, then inspect its actual capture result without adding test memories.
