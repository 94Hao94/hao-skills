#!/usr/bin/env python3
"""Run a reproducible paired read-only CodeGraph pilot benchmark."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PilotRun:
    task_id: str
    enabled: bool
    exit_code: int
    total_tokens: int
    cached_input_tokens: int
    tool_calls: int
    time_to_first_correct_file: Optional[float]
    accuracy: float
    source_verified: bool
    wrong_graph_edge_edit: bool


@dataclass(frozen=True)
class PilotReport:
    runs: Tuple[PilotRun, ...]
    passed: bool
    enabled_median_tokens: float
    disabled_median_tokens: float
    token_gain: float
    enabled_median_time: Optional[float]
    disabled_median_time: Optional[float]
    time_gain: Optional[float]
    enabled_accuracy: float
    disabled_accuracy: float
    reasons: Tuple[str, ...]


def parse_event(line: str, elapsed: float) -> Optional[Tuple[float, Mapping[str, Any]]]:
    """Parse one JSONL object without propagating its raw content on failure."""

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return elapsed, payload


def _agent_text(event: Mapping[str, Any]) -> Optional[str]:
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
        return item["text"]
    return None


def _final_files(events: Sequence[Tuple[float, Mapping[str, Any]]]) -> List[str]:
    for _, event in reversed(events):
        text = _agent_text(event)
        if text is None:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("files"), list):
            return [item for item in payload["files"] if isinstance(item, str)]
    return []


def summarize_events(
    events: Sequence[Tuple[float, Mapping[str, Any]]],
    *,
    task_id: str,
    enabled: bool,
    expected_files: Sequence[str],
    exit_code: int,
    final_files: Optional[Sequence[str]] = None,
) -> PilotRun:
    """Reduce streamed Codex events to bounded, comparable metrics."""

    expected = set(expected_files)
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    first_file = None
    for elapsed, event in events:
        if first_file is None:
            serialized = json.dumps(event, ensure_ascii=False, sort_keys=True)
            if any(path in serialized for path in expected):
                first_file = elapsed
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") in {
                "mcp_tool_call", "function_call", "command_execution", "web_search"
            }:
                tool_calls += 1
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
                cached_tokens += int(usage.get("cached_input_tokens", 0) or 0)
    files = list(final_files) if final_files is not None else _final_files(events)
    accurate = exit_code == 0 and bool(expected.intersection(files))
    return PilotRun(
        task_id=task_id,
        enabled=enabled,
        exit_code=exit_code,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=cached_tokens,
        tool_calls=tool_calls,
        time_to_first_correct_file=first_file,
        accuracy=1.0 if accurate else 0.0,
        source_verified=accurate,
        wrong_graph_edge_edit=False,
    )


def _gain(enabled: float, disabled: float) -> float:
    return 0.0 if disabled <= 0 else (disabled - enabled) / disabled


def _median_time(runs: Sequence[PilotRun]) -> Optional[float]:
    values = [run.time_to_first_correct_file for run in runs if run.time_to_first_correct_file is not None]
    return statistics.median(values) if len(values) == len(runs) and values else None


def evaluate_report(runs: Sequence[PilotRun]) -> PilotReport:
    """Apply the fixed 20 percent gain and no-regression acceptance contract."""

    enabled = [run for run in runs if run.enabled]
    disabled = [run for run in runs if not run.enabled]
    enabled_tokens = statistics.median([run.total_tokens for run in enabled]) if enabled else 0.0
    disabled_tokens = statistics.median([run.total_tokens for run in disabled]) if disabled else 0.0
    token_gain = _gain(enabled_tokens, disabled_tokens)
    enabled_time = _median_time(enabled)
    disabled_time = _median_time(disabled)
    time_gain = (
        _gain(enabled_time, disabled_time)
        if enabled_time is not None and disabled_time is not None
        else None
    )
    enabled_accuracy = statistics.mean([run.accuracy for run in enabled]) if enabled else 0.0
    disabled_accuracy = statistics.mean([run.accuracy for run in disabled]) if disabled else 0.0
    reasons = []
    enabled_ids = {run.task_id for run in enabled}
    disabled_ids = {run.task_id for run in disabled}
    if len(enabled) != 8 or len(disabled) != 8 or enabled_ids != disabled_ids:
        reasons.append("pilot requires eight paired runs per arm")
    if any(run.exit_code != 0 for run in runs):
        reasons.append("every pilot process must succeed")
    if enabled_accuracy < disabled_accuracy:
        reasons.append("CodeGraph arm accuracy declined")
    if token_gain < 0.20 and (time_gain is None or time_gain < 0.20):
        reasons.append("median token/time gain is below 20 percent")
    if any(not run.source_verified for run in runs):
        reasons.append("a modification claim was not source verified")
    if any(run.wrong_graph_edge_edit for run in runs):
        reasons.append("a wrong graph edge caused an edit")
    return PilotReport(
        runs=tuple(runs),
        passed=not reasons,
        enabled_median_tokens=float(enabled_tokens),
        disabled_median_tokens=float(disabled_tokens),
        token_gain=token_gain,
        enabled_median_time=enabled_time,
        disabled_median_time=disabled_time,
        time_gain=time_gain,
        enabled_accuracy=enabled_accuracy,
        disabled_accuracy=disabled_accuracy,
        reasons=tuple(reasons),
    )


def build_command(
    codex: Path,
    repo: Path,
    response_schema: Path,
    query: str,
    enabled: bool,
) -> List[str]:
    return [
        str(codex),
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--output-schema",
        str(response_schema),
        "--json",
        "-C",
        str(repo),
        "-c",
        "mcp_servers.codegraph.enabled=%s" % ("true" if enabled else "false"),
        query,
    ]


def _stream_command(command: Sequence[str], timeout: float) -> Tuple[int, List[Tuple[float, Mapping[str, Any]]], List[str]]:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    events: List[Tuple[float, Mapping[str, Any]]] = []
    raw_lines: List[str] = []
    deadline = started + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                break
            ready = selector.select(min(0.25, remaining))
            if not ready and process.poll() is not None:
                break
            for key, _ in ready:
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                if key.data != "stdout":
                    continue
                elapsed = time.monotonic() - started
                raw_lines.append(line.rstrip("\n"))
                parsed = parse_event(line, elapsed)
                if parsed is not None:
                    events.append(parsed)
        try:
            exit_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            exit_code = 124
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return exit_code, events, raw_lines


def _load_tasks(path: Path) -> List[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 8:
        raise ValueError("pilot manifest must contain exactly eight tasks")
    for item in payload:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("query"), str)
            or not isinstance(item.get("expected_files"), list)
            or not all(isinstance(value, str) for value in item["expected_files"])
        ):
            raise ValueError("invalid pilot task manifest")
    return payload


def _write_raw(path: Path, events: Sequence[Tuple[float, Mapping[str, Any]]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for elapsed, event in events:
            json.dump({"elapsed": elapsed, "event": event}, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")


def _summary(report: PilotReport) -> str:
    lines = [
        "# CodeGraph Pilot Summary",
        "",
        "Result: **%s**" % ("PASS" if report.passed else "FAIL"),
        "",
        "| Arm | Median tokens | Median first correct file (s) | Accuracy |",
        "|---|---:|---:|---:|",
        "| CodeGraph | %.0f | %s | %.3f |" % (
            report.enabled_median_tokens,
            "n/a" if report.enabled_median_time is None else "%.3f" % report.enabled_median_time,
            report.enabled_accuracy,
        ),
        "| Baseline | %.0f | %s | %.3f |" % (
            report.disabled_median_tokens,
            "n/a" if report.disabled_median_time is None else "%.3f" % report.disabled_median_time,
            report.disabled_accuracy,
        ),
        "",
        "Token gain: %.1f%%" % (report.token_gain * 100),
        "Time gain: %s" % ("n/a" if report.time_gain is None else "%.1f%%" % (report.time_gain * 100)),
        "",
        "| Task | Arm | Exit | Tokens | Tool calls | Accuracy |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for run in report.runs:
        lines.append(
            "| %s | %s | %d | %d | %d | %.1f |"
            % (run.task_id, "CodeGraph" if run.enabled else "Baseline", run.exit_code, run.total_tokens, run.tool_calls, run.accuracy)
        )
    if report.reasons:
        lines.extend(["", "Acceptance failures:"])
        lines.extend("- " + reason for reason in report.reasons)
    return "\n".join(lines) + "\n"


def run_pilot(
    repo: Path,
    tasks_path: Path,
    response_schema: Path,
    codex: Path,
    cache_root: Path,
    *,
    timeout: float = 180.0,
) -> Tuple[PilotReport, Path]:
    tasks = _load_tasks(tasks_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = cache_root / (stamp + "-%d" % os.getpid())
    output.mkdir(parents=True, exist_ok=False)
    runs = []
    for index, task in enumerate(tasks):
        arms = (True, False) if index % 2 == 0 else (False, True)
        for enabled in arms:
            command = build_command(codex, repo, response_schema, task["query"], enabled)
            exit_code, events, _ = _stream_command(command, timeout)
            arm = "codegraph" if enabled else "baseline"
            _write_raw(output / (task["id"] + "-" + arm + ".jsonl"), events)
            runs.append(
                summarize_events(
                    events,
                    task_id=task["id"],
                    enabled=enabled,
                    expected_files=task["expected_files"],
                    exit_code=exit_code,
                )
            )
    report = evaluate_report(runs)
    (output / "summary.md").write_text(_summary(report), encoding="utf-8")
    (output / "metrics.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report, output


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--response-schema", required=True)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    codex = shutil.which(args.codex_bin) if Path(args.codex_bin).name == args.codex_bin else args.codex_bin
    if not codex:
        print("FAIL: codex executable not found", file=sys.stderr)
        return 1
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "setup-dev-spec-memory" / "codegraph-pilot"
    try:
        report, output = run_pilot(
            Path(args.repo).expanduser().resolve(),
            Path(args.tasks).expanduser().resolve(),
            Path(args.response_schema).expanduser().resolve(),
            Path(codex).resolve(),
            cache,
            timeout=args.timeout,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    print("%s: %s" % ("PASS" if report.passed else "FAIL", output / "summary.md"))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
