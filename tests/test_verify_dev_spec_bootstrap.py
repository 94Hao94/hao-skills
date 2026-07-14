import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.dev_spec_contract import build_contract, parse_spec
from scripts.install_dev_spec_bootstrap import apply_install_plan, build_install_plan
from scripts.verify_dev_spec_bootstrap import verify_live, verify_static


ROOT = Path(__file__).resolve().parents[1]


class _VerifyHandler(BaseHTTPRequestHandler):
    flags_payload = {"flags": []}
    slots_payload = {"slots": []}
    memories_payload = {"memories": []}

    def do_GET(self):
        if self.path == "/agentmemory/config/flags":
            payload = type(self).flags_payload
        elif self.path == "/agentmemory/slots":
            payload = type(self).slots_payload
        elif self.path.startswith("/agentmemory/memories?"):
            payload = type(self).memories_payload
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.spec = self.home / "AI开发执行规范.md"
        self.spec.write_text(
            "# AI 开发执行规范\n\n> 规范版本：v1.6\n",
            encoding="utf-8",
        )
        (self.home / ".agentmemory").mkdir()
        (self.home / ".codex").mkdir()
        changes = build_install_plan(self.home, self.spec, ROOT)
        apply_install_plan(changes, timestamp="20260713-120000")

        _VerifyHandler.flags_payload = {
            "flags": [
                {"key": "AGENTMEMORY_SLOTS", "enabled": True},
                {"key": "AGENTMEMORY_INJECT_CONTEXT", "enabled": True},
            ]
        }
        contract = build_contract(parse_spec(self.spec), self.spec)
        _VerifyHandler.slots_payload = {
            "slots": [
                {
                    "label": "ai_dev_spec_bootstrap",
                    "scope": "global",
                    "pinned": True,
                    "content": contract,
                }
            ]
        }
        _VerifyHandler.memories_payload = {
            "memories": [
                {
                    "id": "mem_index",
                    "content": (
                        "[Codex] AI开发执行规范 v1.6 - 当前索引\n"
                        "SHA-256: %s\nbootstrap_slot: ai_dev_spec_bootstrap"
                        % parse_spec(self.spec).sha256
                    ),
                }
            ]
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _VerifyHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def test_static_installation_passes(self):
        checks = verify_static(self.home, self.spec, ROOT)
        self.assertTrue(all(check.ok for check in checks), checks)

    def test_live_flags_and_slot_pass(self):
        meta = parse_spec(self.spec)
        checks = verify_live(self.url, "", meta.version, meta.sha256)
        self.assertTrue(all(check.ok for check in checks), checks)

    def test_disabled_slot_flag_fails(self):
        _VerifyHandler.flags_payload = {
            "flags": [
                {"key": "AGENTMEMORY_SLOTS", "enabled": False},
                {"key": "AGENTMEMORY_INJECT_CONTEXT", "enabled": True},
            ]
        }

        meta = parse_spec(self.spec)
        checks = verify_live(self.url, "", meta.version, meta.sha256)

        failed = [check.name for check in checks if not check.ok]
        self.assertIn("live flag AGENTMEMORY_SLOTS", failed)

    def test_missing_current_index_fails(self):
        _VerifyHandler.memories_payload = {"memories": []}
        meta = parse_spec(self.spec)

        checks = verify_live(self.url, "", meta.version, meta.sha256)

        failed = [check.name for check in checks if not check.ok]
        self.assertIn("current specification index", failed)

    def test_stale_slot_contract_fails(self):
        stale = self.home / "stale.md"
        stale.write_text(
            "# AI 开发执行规范\n\n> 规范版本：v1.5\n",
            encoding="utf-8",
        )
        _VerifyHandler.slots_payload["slots"][0]["content"] = build_contract(
            parse_spec(stale), stale
        )
        meta = parse_spec(self.spec)

        checks = verify_live(self.url, "", meta.version, meta.sha256)

        failed = [check.name for check in checks if not check.ok]
        self.assertIn("bootstrap contract content", failed)


if __name__ == "__main__":
    unittest.main()
