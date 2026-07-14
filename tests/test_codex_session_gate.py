import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.dev_spec_contract import build_contract, parse_spec


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "codex_session_gate.py"


class _SlotHandler(BaseHTTPRequestHandler):
    status = 200
    payload = {"slots": []}

    def do_GET(self):
        if self.path != "/agentmemory/slots":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spec = Path(self.tmp.name) / "AI开发执行规范.md"
        self.spec.write_text(
            "# AI 开发执行规范\n\n> 规范版本：v1.6\n",
            encoding="utf-8",
        )
        _SlotHandler.status = 200
        _SlotHandler.payload = {"slots": []}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SlotHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def run_gate(self, spec_path=None):
        env = os.environ.copy()
        env["AGENTMEMORY_URL"] = "http://127.0.0.1:%d" % self.server.server_port
        env["AI_DEV_SPEC_PATH"] = str(spec_path or self.spec)
        completed = subprocess.run(
            [sys.executable, str(GATE)],
            input=json.dumps(
                {
                    "session_id": "gate-test",
                    "cwd": self.tmp.name,
                    "hook_event_name": "SessionStart",
                    "source": "startup",
                }
            ),
            text=True,
            capture_output=True,
            env=env,
            cwd=str(ROOT),
            timeout=5,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_valid_global_slot_is_injected(self):
        contract = build_contract(parse_spec(self.spec), self.spec)
        _SlotHandler.payload = {
            "slots": [
                {
                    "label": "ai_dev_spec_bootstrap",
                    "scope": "global",
                    "pinned": True,
                    "content": contract,
                }
            ]
        }

        result = self.run_gate()

        self.assertTrue(result["continue"])
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP", context)
        self.assertNotIn("DEGRADED_LOCAL_AUTHORITY", context)

    def test_missing_slot_uses_local_spec(self):
        _SlotHandler.status = 503

        result = self.run_gate()

        self.assertTrue(result["continue"])
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("DEGRADED_LOCAL_AUTHORITY", context)
        self.assertIn("v1.6", context)

    def test_stale_global_slot_uses_current_local_spec(self):
        stale = Path(self.tmp.name) / "stale.md"
        stale.write_text(
            "# AI 开发执行规范\n\n> 规范版本：v1.5\n",
            encoding="utf-8",
        )
        _SlotHandler.payload = {
            "slots": [
                {
                    "label": "ai_dev_spec_bootstrap",
                    "scope": "global",
                    "pinned": True,
                    "content": build_contract(parse_spec(stale), stale),
                }
            ]
        }

        result = self.run_gate()

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP v1.6", context)
        self.assertIn("DEGRADED_LOCAL_AUTHORITY", context)

    def test_missing_slot_and_local_spec_fails_closed(self):
        _SlotHandler.status = 503
        missing = Path(self.tmp.name) / "missing.md"

        result = self.run_gate(missing)

        self.assertFalse(result["continue"])
        self.assertIn("development spec bootstrap unavailable", result["stopReason"])
        self.assertIn("Universal development gate blocked", result["systemMessage"])


if __name__ == "__main__":
    unittest.main()
