#!/usr/bin/env python3
"""Codex-only agentmemory writer: no retrieval, context injection or plugin dependency."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

HOOKS = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt_submit",
    "PostToolUse": "post_tool_use",
    "PostToolUseFailure": "post_tool_failure",
    "PreCompact": "pre_compact",
    "Stop": "stop",
    "SessionEnd": "session_end",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "TaskCompleted": "task_completed",
    "Notification": "notification",
}
ALLOWED_ENDPOINTS = {
    "/agentmemory/observe",
    "/agentmemory/session/end",
    "/agentmemory/remember",
    "/agentmemory/lessons",
}
TOOL_FIELDS = {
    "memory_save": {
        "content": "string",
        "type": "string",
        "concepts": "string",
        "files": "string",
        "project": "string",
        "agentId": "string",
    },
    "memory_lesson_save": {
        "content": "string",
        "context": "string",
        "confidence": "number",
        "project": "string",
        "tags": "string",
    },
}
SECRET_KEY = re.compile(
    r"^(?:api[_-]?key|secret|token|password|credential|authorization|access_token|refresh_token|image_data)$",
    re.I,
)
SECRET_PATTERNS = [
    re.compile(r"<private>[\s\S]*?</private>", re.I),
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|$)"
    ),
    re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password|credential|authorization)\s*[=:]\s*[\"\']?[^\s\"\',;}]{8,}"
    ),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(
        r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]+|AKIA[0-9A-Z]{16}|AIza[A-Za-z0-9_-]{35})\b"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/?#@:]+:[^\s/?#@]+@"),
    re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+"),
]


def scrub(value):
    """Redact before either network transmission or durable local storage."""
    if isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            value = pattern.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if SECRET_KEY.match(str(k))
            or re.search(
                r"(?:^|[_-])(?:secret|token|password|credential|api_key|private_key)$",
                str(k),
                re.I,
            )
            else scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def usable_secret(value):
    value = value.strip() if isinstance(value, str) else ""
    return "" if value.startswith("${") and value.endswith("}") else value


def resolve_secret(url, env=None, home=None):
    """Match the daemon client resolver; local files never authenticate remote URLs."""
    env = os.environ if env is None else env
    explicit = usable_secret(env.get("AGENTMEMORY_SECRET", ""))
    if explicit or env.get("AGENTMEMORY_CREDENTIAL_MODE") == "explicit":
        return explicit
    host = urlsplit(url).hostname
    try:
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        local = host == "localhost"
    if not local:
        return ""
    root = (Path.home() if home is None else home) / ".agentmemory"
    found = ""
    try:
        for line in (root / ".env").read_text().splitlines():
            match = re.match(
                r"^\s*(?:export\s+)?AGENTMEMORY_SECRET\s*=\s*(.*?)\s*$", line
            )
            if match:
                value = match.group(1)
                if value.startswith(('"', "'")) and value[0] in value[1:]:
                    value = value[1 : value.index(value[0], 1)]
                else:
                    value = value.split(" #", 1)[0]
                found = usable_secret(value)
    except OSError:
        pass
    if found:
        return found
    try:
        return usable_secret((root / "secret").read_text())
    except OSError:
        return ""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post(url, endpoint, body, timeout):
    if endpoint not in ALLOWED_ENDPOINTS:
        raise ValueError("endpoint outside capture whitelist")
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("invalid daemon URL")
    headers = {"Content-Type": "application/json"}
    secret = resolve_secret(url)
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = Request(
        url.rstrip("/") + endpoint,
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
            try:
                result = json.loads(raw)
                if not isinstance(result, dict):
                    result = {}
            except ValueError:
                result = {}
            return response.status, result
    except HTTPError as exc:
        return exc.code, {}  # Never echo response bodies (may include secrets).


def project_for(cwd):
    explicit = os.environ.get("AGENTMEMORY_PROJECT_NAME", "").strip()
    if explicit:
        return explicit
    try:
        common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd,
            timeout=0.5,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        path = (Path(cwd) / common).resolve()
        if path.name == ".git":
            return path.parent.name
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd).name or "unknown"


def should_capture_tool(name):
    full = str(name or "").lower()
    bare = full.split("__")[-1]
    # Memory tools are always denied, including if an allowlist is configured.
    if (
        bare.startswith("memory_")
        or "agentmemory" in full
        or "evolving_profile" in full
        or "evolving-profile" in full
        or bare in ("toolsearch", "listmcpresources", "fetchmcpresource")
    ):
        return False
    allow = re.split(r"[,\s]+", os.environ.get("AGENTMEMORY_CAPTURE_ALLOW", "").strip())
    deny = re.split(r"[,\s]+", os.environ.get("AGENTMEMORY_CAPTURE_DENY", "").strip())

    def matches(patterns):
        """Match host tool names against configured capture allow/deny patterns."""
        return any(
            p
            and (
                fnmatch.fnmatchcase(full, p.lower())
                or fnmatch.fnmatchcase(bare, p.lower())
            )
            for p in patterns
        )

    return matches(allow) if any(allow) else not matches(deny)


def event_records(event, payload):
    """Translate lifecycle events without calling /session/start (which retrieves)."""
    hook = HOOKS.get(event)
    if not hook or payload.get("entrypoint") == "sdk-ts":
        return []
    session = (
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("conversation_id")
    )
    if not session:  # Never mix unidentified sessions into a global "unknown" bucket.
        return []
    cwd = (
        payload.get("cwd")
        or next(iter(payload.get("workspace_roots", [])), None)
        or os.getcwd()
    )
    metadata = {
        "source_agent": "codex",
        "agent_id": payload.get("agent_id")
        or payload.get("agentId")
        or os.environ.get("AGENT_ID", "codex"),
        "model": payload.get("model")
        or payload.get("model_id")
        or os.environ.get("CODEX_MODEL"),
        "session_id": session,
    }
    if hook == "prompt_submit":
        data = {"prompt": payload.get("prompt", payload.get("userPrompt", ""))}
    elif hook in ("post_tool_use", "post_tool_failure"):
        name = payload.get("tool_name", payload.get("toolName"))
        if not should_capture_tool(name):
            return []
        output = payload.get(
            "tool_response",
            payload.get(
                "tool_output",
                payload.get(
                    "tool_result", payload.get("toolResult", payload.get("error"))
                ),
            ),
        )
        output = scrub(output)
        encoded = (
            json.dumps(output, ensure_ascii=False)
            if not isinstance(output, str)
            else output
        )
        try:
            limit = max(
                256,
                min(
                    100000,
                    int(os.environ.get("AGENTMEMORY_CAPTURE_OUTPUT_MAX", "8000")),
                ),
            )
        except ValueError:
            limit = 8000
        data = {
            "tool_name": name,
            "tool_input": payload.get("tool_input", payload.get("toolArgs")),
            "tool_output": encoded[:limit]
            + ("...[truncated]" if len(encoded) > limit else ""),
        }
    else:
        # Lifecycle metadata only; do not open/read transcript paths or re-inject context.
        data = {
            k: payload[k]
            for k in ("source", "reason", "trigger", "notification_type", "agent_type")
            if k in payload
        }
    data["capture_metadata"] = metadata
    now = time.time()
    stamp = payload.get("timestamp") or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)
    )
    identity = next(
        (
            str(payload[key])
            for key in (
                "event_id",
                "eventId",
                "tool_use_id",
                "toolUseId",
                "tool_call_id",
                "call_id",
                "prompt_id",
                "message_id",
            )
            if payload.get(key)
        ),
        None,
    )
    identity = identity or (
        str(payload["timestamp"]) + json.dumps(data, sort_keys=True)
        if payload.get("timestamp")
        else uuid.uuid4().hex
    )
    event_id = (
        "codex_"
        + hashlib.sha256(
            (str(session) + "\0" + hook + "\0" + identity).encode()
        ).hexdigest()
    )
    body = scrub(
        {
            "hookType": hook,
            "sessionId": str(session),
            "project": project_for(cwd),
            "cwd": str(cwd),
            "timestamp": str(stamp),
            "eventId": event_id,
            "data": data,
        }
    )
    records = [(event_id, "/agentmemory/observe", body)]
    if hook == "session_end":
        records.append(
            (
                event_id + "_end",
                "/agentmemory/session/end",
                {"sessionId": str(session), "final": True},
            )
        )
    return records


class Queue:
    """Fsync-backed SQLite outbox; failed/rejected rows remain inspectable."""

    def __init__(self, path, url):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=2.5)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, target TEXT, endpoint TEXT, body TEXT, state TEXT, attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, boot TEXT, durable_at REAL DEFAULT 0, last_status INTEGER, created REAL)"
        )
        self.db.commit()
        self.url = url.rstrip("/")
        self.client_session = uuid.uuid4().hex

    def enqueue(self, event_id, endpoint, body):
        if endpoint not in ALLOWED_ENDPOINTS:
            raise ValueError("endpoint outside capture whitelist")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO events (id,target,endpoint,body,state,created) VALUES (?,?,?,?,?,?)",
                (
                    event_id,
                    self.url,
                    endpoint,
                    json.dumps(scrub(body)),
                    "pending",
                    time.time(),
                ),
            )

    def flush(self, timeout=1.0, budget=2.5):
        deadline = time.monotonic() + budget
        sent = 0
        # A SQLite write transaction serializes drainers across both hook and MCP processes.
        while time.monotonic() < deadline:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT id,endpoint,body,attempts,boot,durable_at,state FROM events WHERE target=? AND state IN ('pending','accepted') AND next_at<=? ORDER BY created,id LIMIT 1",
                    (self.url, time.time()),
                ).fetchone()
                if not row:
                    self.db.commit()
                    break
                event_id, endpoint, encoded, attempts, boot, durable_at, state = row
                try:
                    status, result = post(
                        self.url,
                        endpoint,
                        json.loads(encoded),
                        max(0.05, min(timeout, deadline - time.monotonic())),
                    )
                except (OSError, URLError, TimeoutError, ValueError):
                    status, result = 0, {}
                now = time.time()
                ok = (
                    200 <= status < 300
                    and result.get("success") is not False
                    and not result.get("error")
                )
                if ok:
                    new_boot, after = result.get("bootId"), result.get("durableAfterMs")
                    if (
                        isinstance(new_boot, str)
                        and isinstance(after, (int, float))
                        and after > 0
                    ):
                        # Retain until a post-deadline reply from the same daemon boot.
                        if (
                            state == "accepted"
                            and boot == new_boot
                            and now >= durable_at
                        ):
                            new_state, next_at = "delivered", 0
                        else:
                            durable_at = now + min(after / 1000, 600)
                            new_state, next_at = "accepted", durable_at
                        boot = new_boot
                    else:
                        new_state, next_at = "delivered", 0
                    sent += 1
                elif status and status not in (408, 429) and status < 500:
                    new_state, next_at = "rejected", 0
                else:
                    new_state, next_at = (
                        "pending",
                        now + min(300, 2 ** min(attempts, 8)),
                    )
                # Retain only the event hash after durable acknowledgement, not private payloads.
                body = "{}" if new_state == "delivered" else encoded
                self.db.execute(
                    "UPDATE events SET body=?,state=?,attempts=attempts+1,next_at=?,boot=?,durable_at=?,last_status=? WHERE id=?",
                    (body, new_state, next_at, boot, durable_at, status, event_id),
                )
                self.db.commit()
                if not ok and new_state == "pending":
                    break  # Do not turn an offline daemon into repeated blocking hook requests.
            except sqlite3.OperationalError:
                self.db.rollback()
                break
        return sent

    def status(self, event_id=None):
        if event_id:
            row = self.db.execute(
                "SELECT state FROM events WHERE id=?", (event_id,)
            ).fetchone()
            return row[0] if row else "missing"
        return dict(
            self.db.execute(
                "SELECT state,count(*) FROM events WHERE target=? GROUP BY state",
                (self.url,),
            )
        )


def capture_disabled():
    return (
        os.environ.get("CODEX_AGENTMEMORY_CAPTURE", "").lower() in ("0", "false", "off")
        or os.environ.get("AGENTMEMORY_CAPTURE", "").lower() in ("0", "false", "off")
        or os.environ.get("AGENTMEMORY_SDK_CHILD") == "1"
    )


def mcp_response(message, queue):
    """Minimal newline-delimited MCP stdio surface with a hard write-tool whitelist."""
    method, args = message.get("method"), message.get("params", {})
    if method == "initialize":
        return {
            "protocolVersion": args.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "codex-agentmemory-write", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": name,
                    "description": "Write to agentmemory; retrieval belongs to EP. Failed requests remain in a local retry queue.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {k: {"type": v} for k, v in fields.items()},
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                }
                for name, fields in TOOL_FIELDS.items()
            ]
        }
    if method != "tools/call":
        raise ValueError("unsupported MCP method")
    name, values = args.get("name"), args.get("arguments", {})
    if name not in TOOL_FIELDS:
        raise ValueError("tool outside write whitelist")
    if capture_disabled():
        return {
            "isError": True,
            "content": [
                {"type": "text", "text": "Codex agentmemory writes are disabled."}
            ],
        }
    if not isinstance(values, dict) or set(values) - set(TOOL_FIELDS[name]):
        raise ValueError("unsupported write parameters")
    if not isinstance(values.get("content"), str) or not values["content"].strip():
        raise ValueError("content is required")
    for key, value in values.items():
        expected = TOOL_FIELDS[name][key]
        if expected == "string" and not isinstance(value, str):
            raise ValueError("invalid parameter type")
        if expected == "number" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 1
        ):
            raise ValueError("confidence must be between zero and one")
    values = dict(values)
    if name == "memory_save":
        for key in ("concepts", "files"):
            if key in values:
                values[key] = [v.strip() for v in values[key].split(",") if v.strip()]
    event_id = (
        "write_"
        + hashlib.sha256(
            json.dumps(
                [queue.client_session, message.get("id"), name, values], sort_keys=True
            ).encode()
        ).hexdigest()
    )
    queue.enqueue(
        event_id,
        "/agentmemory/remember" if name == "memory_save" else "/agentmemory/lessons",
        values,
    )
    queue.flush()
    state = queue.status(event_id)
    return {
        "isError": state != "delivered",
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "event_id": event_id,
                        "status": state,
                        "saved": state == "delivered",
                    }
                ),
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queue",
        type=Path,
        default=Path(
            os.environ.get(
                "CODEX_AGENTMEMORY_QUEUE",
                str(Path.home() / ".codex" / "agentmemory-capture" / "queue.sqlite3"),
            )
        ),
    )
    parser.add_argument(
        "--url", default=os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    hook = commands.add_parser("hook")
    hook.add_argument("event", choices=HOOKS)
    commands.add_parser("mcp")
    commands.add_parser("flush")
    commands.add_parser("status")
    args = parser.parse_args()
    if args.command != "status" and capture_disabled():
        # MCP must still initialize and list tools while writes are opted out.
        if args.command != "mcp":
            return 0
    queue = Queue(args.queue, args.url)
    if args.command == "hook":
        payload = json.loads(sys.stdin.read(2 * 1024 * 1024))
        if not isinstance(payload, dict):
            return 0
        for event_id, endpoint, body in event_records(args.event, payload):
            queue.enqueue(event_id, endpoint, body)
        queue.flush()
    elif args.command == "mcp":
        for line in sys.stdin:
            message = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict) or "id" not in message:
                    continue
                result = mcp_response(message, queue)
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            except (ValueError, OSError, sqlite3.Error) as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id") if isinstance(message, dict) else None,
                    "error": {
                        "code": -32602,
                        "message": "Write adapter rejected request: "
                        + type(exc).__name__,
                    },
                }
            print(json.dumps(response), flush=True)
    elif args.command == "flush":
        queue.flush(budget=20)
        print(json.dumps(queue.status()))
    else:
        print(json.dumps(queue.status()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error):
        print(
            "Codex agentmemory capture failed; inspect the local queue.",
            file=sys.stderr,
        )
        raise SystemExit(1)
