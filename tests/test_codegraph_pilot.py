import json
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_pilot import (
    PilotRun,
    build_command,
    evaluate_report,
    parse_event,
    summarize_events,
)


class CodeGraphPilotTests(unittest.TestCase):
    def test_jsonl_parser_counts_tokens_tools_and_first_correct_file(self):
        events = [
            (0.2, {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "codegraph", "tool": "codegraph_explore"}}),
            (1.1, {"type": "item.completed", "item": {"type": "agent_message", "text": "scripts/dev_spec_contract.py"}}),
            (2.0, {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 100, "output_tokens": 200}}),
        ]
        run = summarize_events(
            events,
            task_id="bootstrap-render",
            enabled=True,
            expected_files=["scripts/dev_spec_contract.py"],
            exit_code=0,
            final_files=["scripts/dev_spec_contract.py"],
        )
        self.assertEqual(run.tool_calls, 1)
        self.assertEqual(run.total_tokens, 1100)
        self.assertEqual(run.cached_input_tokens, 100)
        self.assertEqual(run.time_to_first_correct_file, 1.1)
        self.assertEqual(run.accuracy, 1.0)

    def test_absolute_final_file_matches_repository_relative_expectation(self):
        run = summarize_events(
            [],
            task_id="bootstrap-render",
            enabled=True,
            expected_files=["scripts/dev_spec_contract.py"],
            exit_code=0,
            final_files=["/Users/example/repo/scripts/dev_spec_contract.py"],
        )
        self.assertEqual(run.accuracy, 1.0)

    def test_parse_event_rejects_non_object_json_without_leaking_line(self):
        self.assertIsNone(parse_event("not-json", 0.5))
        self.assertIsNone(parse_event("[]", 0.5))
        self.assertEqual(parse_event('{"type":"turn.started"}', 0.5)[0], 0.5)

    def test_success_requires_twenty_percent_median_gain_without_accuracy_loss(self):
        report = evaluate_report(self.paired_runs(with_tokens=800, without_tokens=1000, with_accuracy=1.0, without_accuracy=1.0))
        self.assertTrue(report.passed)
        report = evaluate_report(self.paired_runs(with_tokens=700, without_tokens=1000, with_accuracy=0.75, without_accuracy=1.0))
        self.assertFalse(report.passed)

    def test_time_gain_can_pass_when_tokens_do_not(self):
        runs = self.paired_runs(with_tokens=1000, without_tokens=1000, with_accuracy=1.0, without_accuracy=1.0)
        runs = [
            PilotRun(**{**run.__dict__, "time_to_first_correct_file": 8.0 if run.enabled else 10.0})
            for run in runs
        ]
        self.assertTrue(evaluate_report(runs).passed)

    def test_failed_or_unverified_runs_fail_the_report(self):
        runs = self.paired_runs(with_tokens=700, without_tokens=1000, with_accuracy=1.0, without_accuracy=1.0)
        runs[0] = PilotRun(**{**runs[0].__dict__, "source_verified": False})
        self.assertFalse(evaluate_report(runs).passed)
        runs = self.paired_runs(with_tokens=700, without_tokens=1000, with_accuracy=1.0, without_accuracy=1.0)
        runs[0] = PilotRun(**{**runs[0].__dict__, "wrong_graph_edge_edit": True})
        self.assertFalse(evaluate_report(runs).passed)

    def test_command_is_read_only_ephemeral_and_disables_only_selected_arm(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            schema = Path(temporary) / "response.schema.json"
            enabled = build_command(Path("/opt/codex"), repo, schema, "question", True)
            disabled = build_command(Path("/opt/codex"), repo, schema, "question", False)
        self.assertEqual(enabled[:6], ["/opt/codex", "exec", "--ephemeral", "--sandbox", "read-only", "--output-schema"])
        self.assertIn("mcp_servers.codegraph.enabled=true", enabled)
        self.assertIn("mcp_servers.codegraph.enabled=false", disabled)
        self.assertEqual(enabled[-1], "question")

    def paired_runs(self, with_tokens, without_tokens, with_accuracy, without_accuracy):
        runs = []
        for index in range(8):
            for enabled, tokens, accuracy in (
                (True, with_tokens, with_accuracy),
                (False, without_tokens, without_accuracy),
            ):
                runs.append(
                    PilotRun(
                        task_id="task-%d" % index,
                        enabled=enabled,
                        exit_code=0,
                        total_tokens=tokens,
                        cached_input_tokens=0,
                        tool_calls=1 if enabled else 0,
                        time_to_first_correct_file=1.0,
                        accuracy=accuracy,
                        source_verified=True,
                        wrong_graph_edge_edit=False,
                    )
                )
        return runs


if __name__ == "__main__":
    unittest.main()
