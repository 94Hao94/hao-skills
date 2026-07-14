#!/usr/bin/env python3
"""Inject the universal development-spec contract into Codex SessionStart."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_spec_contract import (
    SLOT_LABEL,
    build_contract,
    parse_spec,
    validate_contract,
)


REQUEST_TIMEOUT_SECONDS = 1.5


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _slots_from_payload(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("slots", payload.get("items", []))
    else:
        candidates = []
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def fetch_global_contract(
    base_url: str,
    secret: str = "",
    expected_version: str = "",
    expected_sha256: str = "",
) -> str:
    """Fetch and validate the fixed global pinned bootstrap slot."""

    url = base_url.rstrip("/") + "/agentmemory/slots"
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    for slot in _slots_from_payload(payload):
        if slot.get("label") != SLOT_LABEL:
            continue
        if slot.get("scope") != "global" or not _is_true(slot.get("pinned")):
            raise ValueError("bootstrap slot is not global and pinned")
        content = slot.get("content")
        if not isinstance(content, str):
            raise ValueError("bootstrap slot content is missing")
        missing = validate_contract(content)
        if missing:
            raise ValueError("bootstrap contract is missing required tokens")
        if expected_version and "AI_DEV_SPEC_BOOTSTRAP %s" % expected_version not in content:
            raise ValueError("bootstrap contract version is stale")
        if expected_sha256 and "sha256=%s" % expected_sha256 not in content:
            raise ValueError("bootstrap contract SHA-256 is stale")
        return content
    raise ValueError("bootstrap slot is missing")


def _continue_result(content: str) -> Dict[str, Any]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": content,
        },
    }


def _stop_result() -> Dict[str, Any]:
    return {
        "continue": False,
        "stopReason": "development spec bootstrap unavailable: agentmemory slot and local authority both failed",
        "systemMessage": "Universal development gate blocked this session before project work.",
    }


def run_gate() -> Dict[str, Any]:
    """Resolve the global contract, then degrade to the local authority."""

    base_url = os.environ.get("AGENTMEMORY_URL", "http://localhost:3111")
    secret = os.environ.get("AGENTMEMORY_SECRET", "")
    authority = Path(
        os.environ.get("AI_DEV_SPEC_PATH", "~/AI开发执行规范.md")
    ).expanduser()
    try:
        local_meta = parse_spec(authority)
    except (OSError, UnicodeError, ValueError):
        local_meta = None
    try:
        return _continue_result(
            fetch_global_contract(
                base_url,
                secret,
                local_meta.version if local_meta else "",
                local_meta.sha256 if local_meta else "",
            )
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        pass

    if local_meta:
        return _continue_result(build_contract(local_meta, authority, degraded=True))
    return _stop_result()


def main() -> int:
    try:
        raw = sys.stdin.read()
        if raw:
            json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps(_stop_result(), ensure_ascii=False))
        return 0
    print(json.dumps(run_gate(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
