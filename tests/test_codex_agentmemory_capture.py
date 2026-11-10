"""Integration checks use only an ephemeral loopback daemon and private queue."""

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from scripts import codex_agentmemory_capture as capture


class MockDaemon(BaseHTTPRequestHandler):
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.records.append((self.path, data, dict(self.headers)))
        self.send_response(self.server.code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.server.reply).encode())

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def daemon(code=201, reply=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockDaemon)
    server.records = []
    server.code = code
    server.reply = reply or {"success": True}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:" + str(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.credentials = patch.dict(
            os.environ,
            {"AGENTMEMORY_CREDENTIAL_MODE": "explicit", "AGENTMEMORY_SECRET": ""},
        )
        self.credentials.start()
        self.addCleanup(self.credentials.stop)

    def test_lifecycle_does_not_retrieve_or_read_transcript(self):
        with tempfile.TemporaryDirectory() as tmp, daemon() as (server, url):
            queue = capture.Queue(Path(tmp) / "queue.db", url)
            payload = {
                "session_id": "s",
                "cwd": tmp,
                "event_id": "start",
                "model": "test-model",
                "agent_id": "agent-1",
                "transcript_path": "/not/read",
            }
            records = capture.event_records("SessionStart", payload)
            for record in records:
                queue.enqueue(*record)
            queue.flush()
            self.assertEqual([x[0] for x in server.records], ["/agentmemory/observe"])
            data = server.records[0][1]
            self.assertEqual(data["hookType"], "session_start")
            self.assertEqual(data["data"]["capture_metadata"]["model"], "test-model")
            self.assertEqual(data["data"]["capture_metadata"]["agent_id"], "agent-1")
            self.assertNotIn("transcript_path", data["data"])
            self.assertEqual(os.stat(Path(tmp) / "queue.db").st_mode & 0o777, 0o600)
            self.assertEqual(capture.event_records("PreToolUse", payload), [])
            self.assertEqual(capture.event_records("SessionStart", {}), [])

    def test_prompt_and_tool_events_preserve_identity_and_redact(self):
        payload = {
            "session_id": "s",
            "cwd": "/tmp",
            "event_id": "x",
            "prompt": "hello <private>do not store</private> token=secretabcdefghijk",
        }
        first = capture.event_records("UserPromptSubmit", payload)[0]
        again = capture.event_records("UserPromptSubmit", payload)[0]
        self.assertEqual(first[0], again[0])
        self.assertNotIn("secretabcdefghijk", json.dumps(first))
        payload.update(
            tool_name="exec_command",
            tool_input={"AGENTMEMORY_SECRET": "hidden"},
            tool_output={"access_token": "hidden", "result": "fine"},
        )
        event = capture.event_records("PostToolUse", payload)[0]
        self.assertNotIn("hidden", json.dumps(event))
        self.assertIn("fine", json.dumps(event))
        payload.pop("event_id")
        one = capture.event_records("PostToolUse", payload)[0]
        two = capture.event_records("PostToolUse", payload)[0]
        self.assertNotEqual(
            one[0], two[0]
        )  # Identical legitimate calls remain distinct without host identity.

    def test_memory_tools_never_reenter_capture_even_with_allowlist(self):
        with patch.dict(os.environ, {"AGENTMEMORY_CAPTURE_ALLOW": "*"}):
            for name in (
                "memory_save",
                "mcp__agentmemory__memory_smart_search",
                "mcp__evolving_profile__user_recall",
            ):
                self.assertFalse(capture.should_capture_tool(name))
        with patch.dict(
            os.environ, {"AGENTMEMORY_CAPTURE_DENY": "exec_*"}, clear=False
        ):
            self.assertFalse(capture.should_capture_tool("exec_command"))
            self.assertTrue(capture.should_capture_tool("read_file"))

    def test_session_end_registers_final_write_but_stop_does_not(self):
        payload = {"session_id": "s", "cwd": "/tmp", "event_id": "end"}
        self.assertEqual(
            [r[1] for r in capture.event_records("SessionEnd", payload)],
            ["/agentmemory/observe", "/agentmemory/session/end"],
        )
        self.assertEqual(
            [r[1] for r in capture.event_records("Stop", payload)],
            ["/agentmemory/observe"],
        )

    def test_retry_is_durable_and_duplicate_enqueue_does_not_resend(self):
        with tempfile.TemporaryDirectory() as tmp, daemon(503) as (server, url):
            path = Path(tmp) / "queue.db"
            queue = capture.Queue(path, url)
            queue.enqueue("e", "/agentmemory/observe", {"data": "hello"})
            queue.flush()
            self.assertEqual(queue.status("e"), "pending")
            self.assertEqual(
                queue.db.execute("SELECT attempts FROM events").fetchone()[0], 1
            )
            queue.db.close()
            queue = capture.Queue(path, url)
            server.code = 201
            queue.db.execute("UPDATE events SET next_at=0")
            queue.db.commit()
            queue.flush()
            self.assertEqual(queue.status("e"), "delivered")
            queue.enqueue("e", "/agentmemory/observe", {"data": "hello"})
            queue.flush()
            self.assertEqual(len(server.records), 2)
            self.assertEqual(
                queue.db.execute("SELECT body FROM events").fetchone()[0], "{}"
            )

    def test_rejection_is_retained_without_endless_retry(self):
        with tempfile.TemporaryDirectory() as tmp, daemon(401) as (server, url):
            queue = capture.Queue(Path(tmp) / "queue.db", url)
            queue.enqueue("e", "/agentmemory/observe", {"data": "hello"})
            queue.flush()
            queue.flush()
            self.assertEqual(queue.status("e"), "rejected")
            self.assertEqual(len(server.records), 1)

    def test_accepted_rows_survive_daemon_boot_change(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            daemon(reply={"bootId": "boot-A", "durableAfterMs": 1000}) as (server, url),
        ):
            queue = capture.Queue(Path(tmp) / "queue.db", url)
            queue.enqueue("e", "/agentmemory/observe", {"data": "hello"})
            queue.flush()
            self.assertEqual(queue.status("e"), "accepted")
            server.reply = {"bootId": "boot-B", "durableAfterMs": 1000}
            queue.db.execute("UPDATE events SET next_at=0,durable_at=0")
            queue.db.commit()
            queue.flush()
            self.assertEqual(queue.status("e"), "accepted")
            queue.db.execute("UPDATE events SET next_at=0,durable_at=0")
            queue.db.commit()
            queue.flush()
            self.assertEqual(queue.status("e"), "delivered")
            self.assertEqual(len(server.records), 3)

    def test_concurrent_queues_do_not_duplicate_known_event(self):
        with tempfile.TemporaryDirectory() as tmp, daemon() as (server, url):
            path = Path(tmp) / "queue.db"
            queue = capture.Queue(path, url)
            queue.enqueue("e", "/agentmemory/observe", {"data": "hello"})
            errors = []

            def drain():
                try:
                    capture.Queue(path, url).flush()
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=drain) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(server.records), 1)

    def test_auth_lookup_order_and_remote_local_secret_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".agentmemory").mkdir()
            (home / ".agentmemory" / ".env").write_text(
                'AGENTMEMORY_SECRET="from-env-file"\n'
            )
            (home / ".agentmemory" / "secret").write_text("stored-file")
            self.assertEqual(
                capture.resolve_secret("http://localhost:3111", {}, home),
                "from-env-file",
            )
            self.assertEqual(
                capture.resolve_secret("https://remote.example", {}, home), ""
            )
            self.assertEqual(
                capture.resolve_secret(
                    "http://localhost:3111",
                    {"AGENTMEMORY_CREDENTIAL_MODE": "explicit"},
                    home,
                ),
                "",
            )
            self.assertEqual(
                capture.resolve_secret(
                    "https://remote.example", {"AGENTMEMORY_SECRET": "explicit"}, home
                ),
                "explicit",
            )
            self.assertEqual(
                capture.resolve_secret(
                    "http://localhost:3111",
                    {"AGENTMEMORY_SECRET": "${AGENTMEMORY_SECRET}"},
                    home,
                ),
                "from-env-file",
            )

    def test_mcp_whitelist_and_write_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp, daemon() as (server, url):
            queue = capture.Queue(Path(tmp) / "queue.db", url)
            tools = capture.mcp_response({"method": "tools/list"}, queue)["tools"]
            self.assertEqual(
                {tool["name"] for tool in tools}, {"memory_save", "memory_lesson_save"}
            )
            for index, name in enumerate(("memory_save", "memory_lesson_save")):
                result = capture.mcp_response(
                    {
                        "id": index,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": {"content": "test"}},
                    },
                    queue,
                )
                self.assertFalse(result["isError"])
            self.assertEqual(
                [row[0] for row in server.records],
                ["/agentmemory/remember", "/agentmemory/lessons"],
            )
            self.assertNotIn(
                "agentId", server.records[0][1]
            )  # Omitted means shared, matching native schema.
            with self.assertRaises(ValueError):
                capture.mcp_response(
                    {
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "memory_smart_search",
                            "arguments": {"query": "x"},
                        },
                    },
                    queue,
                )
            with self.assertRaises(ValueError):
                queue.enqueue("bad", "/agentmemory/context", {})

    def test_stdio_protocol_and_opt_out(self):
        script = Path(capture.__file__)
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, CODEX_AGENTMEMORY_CAPTURE="0")
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "memory_save", "arguments": {"content": "x"}},
                },
            ]
            process = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--queue",
                    str(Path(tmp) / "queue.db"),
                    "mcp",
                ],
                input="\n".join(map(json.dumps, requests)) + "\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
            )
            rows = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual([row["id"] for row in rows], [1, 2, 3])
            self.assertTrue(rows[-1]["result"]["isError"])
            self.assertEqual(len(rows[1]["result"]["tools"]), 2)


if __name__ == "__main__":
    unittest.main()
