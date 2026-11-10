import json
import multiprocessing
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest import mock

from scripts import skill_evolution as evolution


BASE = "# Procedure\nCompare inputs directly.\nKeep unrelated values.\nApply only to paths.\n"
FIXED = "# Procedure\nNormalize before comparing inputs.\nKeep unrelated values.\nApply only to paths.\n"

HARNESS = """import json, pathlib, sys
request = json.load(sys.stdin)
text = (pathlib.Path(request["package_root"]) / "references/development-spec.md").read_text()
case = request["case"]["id"]
checks = {
  "target": "Normalize before comparing inputs." in text,
  "normal": "Keep unrelated values." in text,
  "counterexample": "Apply only to paths." in text,
  "scope": "Apply only to paths." in text,
}
print(json.dumps({"passed": checks[case], "scope_matches": request["scope"] == "path-comparison"}))
"""


def concurrent_publish(root, candidate_id, queue, started=None):
    try:
        if started:
            started.set()
        result = evolution.publish(Path(root), candidate_id)
        queue.put(("published", result["version"]))
    except evolution.EvolutionError as exc:
        queue.put(("rejected", str(exc)))


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "skill"
        (self.root / "references").mkdir(parents=True)
        (self.root / "scripts").mkdir()
        (self.root / "SKILL.md").write_text("# Personal development skill\n")
        (self.root / "references/development-spec.md").write_text(BASE)
        (self.root / "scripts/helper.py").write_text("print('original')\n")
        self.base = evolution.initialize_manifest(self.root)
        self.harness = self.parent / "acceptance.py"
        self.harness.write_text(HARNESS)
        self.policy = {
            "harness": [sys.executable, str(self.harness)],
            "harness_artifacts": [str(self.harness)],
            "scope": "path-comparison",
            "approval_source_locator": "user:acceptance-policy-installation",
            "cases": [
                {"id": kind, "kind": kind}
                for kind in ("target", "normal", "counterexample", "scope")
            ],
        }
        evolution.configure_policy(self.root, self.policy)

    def proposal(self, text=FIXED, task="task-1", **changes):
        return {
            "task_id": task,
            "focus": "normalize path comparison",
            "reason": "comparison correction",
            "scope": "path-comparison",
            "source_locators": ["ep:source/task-1#correction"],
            "kind": "inferred_experience",
            "edits": {"references/development-spec.md": text},
            **changes,
        }

    def stage(self, **kwargs):
        return evolution.stage(self.root, self.proposal(**kwargs))

    def test_target_improvement_and_independent_cases_publish(self):
        candidate = self.stage()
        result = evolution.validate(self.root, candidate["id"])
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["cases"]), 4)
        manifest = evolution.publish(self.root, candidate["id"])
        self.assertEqual(manifest["version"], 2)
        self.assertEqual(
            (self.root / "references/development-spec.md").read_text(), FIXED
        )
        self.assertEqual(evolution.verify_manifest(self.root), manifest)

    def test_explicit_correction_still_requires_regression_evidence(self):
        candidate = self.stage(
            kind="user_correction", text=FIXED.replace("Keep unrelated values.\n", "")
        )
        with self.assertRaisesRegex(evolution.EvolutionError, "remains pending"):
            evolution.publish(self.root, candidate["id"])
        self.assertEqual(evolution.verify_manifest(self.root)["version"], 1)

    def test_single_unverified_inference_stays_pending(self):
        candidate = self.stage()
        (self.root / evolution.STATE / "policy.json").unlink()
        with self.assertRaisesRegex(evolution.EvolutionError, "missing JSON"):
            evolution.publish(self.root, candidate["id"])
        self.assertEqual(
            evolution.status(self.root)["candidates"][0]["state"], "pending"
        )

    def test_missing_provenance_and_shadow_logs_rejected(self):
        for changes in ({"source_locators": []}, {"raw_session_log": "transcript"}):
            with (
                self.subTest(changes=changes),
                self.assertRaises(evolution.EvolutionError),
            ):
                self.stage(**changes)

    def test_scope_mismatch_rejected_before_running_harness(self):
        candidate = self.stage(scope="all inputs everywhere")
        with mock.patch.object(evolution, "_run_case") as run:
            with self.assertRaisesRegex(
                evolution.EvolutionError, "scope does not match"
            ):
                evolution.validate(self.root, candidate["id"])
            run.assert_not_called()

    def test_counterexample_and_scope_overgeneralization_block_publication(self):
        candidate = self.stage(
            text=FIXED.replace("Apply only to paths.", "Apply universally.")
        )
        validation = evolution.validate(self.root, candidate["id"])
        self.assertFalse(validation["accepted"])
        with self.assertRaises(evolution.EvolutionError):
            evolution.publish(self.root, candidate["id"])

    def test_positive_score_without_target_improvement_is_insufficient(self):
        candidate = self.stage(text=BASE + "Use clear descriptions.\n")
        self.assertFalse(
            evolution.validate(self.root, candidate["id"])["target_improved"]
        )
        with self.assertRaises(evolution.EvolutionError):
            evolution.publish(self.root, candidate["id"])

    def test_forged_validation_file_cannot_approve_candidate(self):
        candidate = self.stage(text=BASE + "Additional revision.\n")
        directory = self.root / evolution.STATE / "candidates" / candidate["id"]
        (directory / "validation.json").write_text('{"accepted": true}')
        with self.assertRaisesRegex(evolution.EvolutionError, "remains pending"):
            evolution.publish(self.root, candidate["id"])

    def test_verifier_policy_scripts_and_test_changes_are_not_auto_editable(self):
        for path in (
            "scripts/skill_evolution.py",
            "tests/acceptance.py",
            "../escape",
            ".evolution/policy.json",
            "references/evolution.md",
        ):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(evolution.EvolutionError, "cannot alter"),
            ):
                self.stage(edits={path: "change"})

    def test_protected_change_requires_exact_scoped_authorization(self):
        candidate = self.stage(text=FIXED + "Change memory routing.\n")
        with self.assertRaisesRegex(
            evolution.EvolutionError, "scoped user authorization"
        ):
            evolution.validate(self.root, candidate["id"])
        authorization = {
            "source_locator": "user:approve-specific-routing-change",
            "base_sha256": candidate["base_sha256"],
            "candidate_sha256": candidate["candidate_sha256"],
            "changed_paths": candidate["changed_paths"],
            "authorized_scope": "only this routing correction",
        }
        invalid = {**authorization, "candidate_sha256": "bad"}
        with self.assertRaises(evolution.EvolutionError):
            evolution.publish(self.root, candidate["id"], invalid)
        self.assertEqual(
            evolution.publish(self.root, candidate["id"], authorization)["version"], 2
        )

    def test_candidate_and_external_harness_digests_are_bound(self):
        candidate = self.stage()
        self.harness.write_text(HARNESS + "# changed acceptance\n")
        with self.assertRaisesRegex(evolution.EvolutionError, "harness changed"):
            evolution.publish(self.root, candidate["id"])
        self.harness.write_text(HARNESS)
        package = (
            self.root / evolution.STATE / "candidates" / candidate["id"] / "package"
        )
        (package / "references/development-spec.md").write_text(FIXED + "extra")
        with self.assertRaisesRegex(evolution.EvolutionError, "changed after staging"):
            evolution.publish(self.root, candidate["id"])

    def test_stale_baseline_and_one_revision_per_task(self):
        first = self.stage()
        stale = self.stage(task="task-2")
        evolution.publish(self.root, first["id"])
        with self.assertRaisesRegex(evolution.EvolutionError, "stale baseline"):
            evolution.publish(self.root, stale["id"])
        same_task = self.stage(text=FIXED + "Minor followup.\n")
        with self.assertRaisesRegex(evolution.EvolutionError, "one automatic revision"):
            evolution.publish(self.root, same_task["id"])

    def test_two_hosts_publish_same_baseline_exactly_once(self):
        first = self.stage()
        second = self.stage(task="task-2")
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [
            context.Process(
                target=concurrent_publish, args=(str(self.root), candidate["id"], queue)
            )
            for candidate in (first, second)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        outcomes = [queue.get(timeout=3), queue.get(timeout=3)]
        self.assertEqual(
            sorted(item[0] for item in outcomes), ["published", "rejected"]
        )
        self.assertEqual(evolution.verify_manifest(self.root)["version"], 2)

    def test_reader_keeps_manifest_and_text_consistent_while_publisher_waits(self):
        candidate = self.stage()
        context = multiprocessing.get_context("spawn")
        outcomes = context.Queue()
        started = context.Event()
        publisher = context.Process(
            target=concurrent_publish,
            args=(str(self.root), candidate["id"], outcomes, started),
        )
        try:
            with evolution.read_verified_package(self.root) as manifest:
                publisher.start()
                self.assertTrue(started.wait(5))
                with self.assertRaises(queue.Empty):
                    outcomes.get(timeout=0.25)
                self.assertEqual(manifest["version"], 1)
                self.assertEqual(
                    (self.root / "references/development-spec.md").read_text(), BASE
                )
            self.assertEqual(outcomes.get(timeout=10), ("published", 2))
            publisher.join(5)
            self.assertEqual(publisher.exitcode, 0)
            with evolution.read_verified_package(self.root) as manifest:
                self.assertEqual(manifest["version"], 2)
                self.assertEqual(
                    (self.root / "references/development-spec.md").read_text(), FIXED
                )
        finally:
            if publisher.is_alive():
                publisher.terminate()
                publisher.join(5)

    def test_snapshot_rollback_restores_scripts_and_references_and_removes_added_files(
        self,
    ):
        candidate = self.stage()
        published = evolution.publish(self.root, candidate["id"])
        # Simulate a separately authorized full-package maintenance revision.
        (self.root / "scripts/helper.py").write_text("print('new')\n")
        (self.root / "scripts/new-helper.py").write_text("new file\n")
        manual = evolution._manifest(
            self.root, 3, "authorized maintenance", published_tasks=["task-1"]
        )
        evolution._atomic(self.root / evolution.MANIFEST, evolution._json(manual))
        manifest = evolution.rollback(
            self.root, published["previous_snapshot"], "regression found"
        )
        self.assertEqual(manifest["version"], 4)
        self.assertEqual(manifest["package_sha256"], self.base["package_sha256"])
        self.assertEqual(
            (self.root / "scripts/helper.py").read_text(), "print('original')\n"
        )
        self.assertEqual(
            (self.root / "references/development-spec.md").read_text(), BASE
        )
        self.assertFalse((self.root / "scripts/new-helper.py").exists())
        self.assertEqual(manifest["published_tasks"], ["task-1"])

    def test_unrelated_local_edits_are_preserved_by_rejecting_rollback(self):
        candidate = self.stage()
        manifest = evolution.publish(self.root, candidate["id"])
        helper = self.root / "scripts/helper.py"
        helper.write_text("unrelated work\n")
        with self.assertRaisesRegex(evolution.EvolutionError, "integrity mismatch"):
            evolution.rollback(self.root, manifest["previous_snapshot"], "restore")
        self.assertEqual(helper.read_text(), "unrelated work\n")

    def test_interrupted_publish_restores_entire_snapshot(self):
        candidate = self.stage()
        original = evolution._atomic
        raised = False

        def fail_manifest_once(path, raw):
            nonlocal raised
            if path == self.root / evolution.MANIFEST and not raised:
                raised = True
                raise OSError("disk error")
            return original(path, raw)

        with mock.patch.object(evolution, "_atomic", side_effect=fail_manifest_once):
            with self.assertRaisesRegex(OSError, "disk error"):
                evolution.publish(self.root, candidate["id"])
        self.assertEqual(
            evolution.verify_manifest(self.root)["package_sha256"],
            self.base["package_sha256"],
        )
        self.assertFalse((self.root / evolution.STATE / "transaction.json").exists())

    def test_manifest_integrity_covers_tests_and_excludes_caches(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__/ignored.pyc").write_bytes(b"cache")
        self.assertEqual(evolution.verify_manifest(self.root), self.base)
        (self.root / "tests").mkdir()
        (self.root / "tests/acceptance.py").write_text("changed\n")
        with self.assertRaisesRegex(evolution.EvolutionError, "integrity mismatch"):
            evolution.verify_manifest(self.root)
        with self.assertRaises(evolution.EvolutionError):
            evolution.initialize_manifest(
                self.root
            )  # Cannot bless drift by re-initializing.

    def test_crash_marker_blocks_loading_and_recovers_only_recorded_snapshot(self):
        snapshot = evolution._snapshot(self.root, self.base)
        (self.root / "references/development-spec.md").write_text(FIXED)
        transaction = {
            "snapshot_id": snapshot,
            "known_paths": list(self.base["files"]),
            "base_version": 1,
        }
        evolution._atomic(
            self.root / evolution.STATE / "transaction.json",
            evolution._json(transaction),
        )
        with self.assertRaisesRegex(
            evolution.EvolutionError, "unfinished skill publication"
        ):
            evolution.verify_manifest(self.root)
        observed = evolution.status(self.root)
        self.assertEqual(observed["unfinished_transaction"]["snapshot_id"], snapshot)
        self.assertIsNone(observed["manifest"])
        other_snapshot = "v1-other"
        # A syntactically valid, existing different snapshot may not be substituted.
        other = self.root / evolution.STATE / "snapshots" / other_snapshot
        evolution._copy_package(
            self.root / evolution.STATE / "snapshots" / snapshot, other
        )
        with self.assertRaisesRegex(
            evolution.EvolutionError, "interrupted transaction"
        ):
            evolution.rollback(self.root, other_snapshot, "wrong recovery")
        recovered = evolution.rollback(
            self.root, snapshot, "interrupted publication recovery"
        )
        self.assertEqual(recovered["package_sha256"], self.base["package_sha256"])
        self.assertEqual(
            (self.root / "references/development-spec.md").read_text(), BASE
        )

    def test_candidate_cannot_smuggle_verifier_change_after_stage(self):
        candidate = self.stage()
        directory = self.root / evolution.STATE / "candidates" / candidate["id"]
        (directory / "package/scripts/helper.py").write_text("altered verifier")
        metadata = json.loads((directory / "proposal.json").read_text())
        metadata["candidate_sha256"] = evolution.package_digest(directory / "package")
        metadata["changed_paths"].append("scripts/helper.py")
        (directory / "proposal.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(
            evolution.EvolutionError, "fixed automatic evolution boundary"
        ):
            evolution.publish(self.root, candidate["id"])


if __name__ == "__main__":
    unittest.main()
