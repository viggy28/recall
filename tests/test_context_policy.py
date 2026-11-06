import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import recall
from recall_core.context_policy import (
    V1_MARKER as SCHEMA_MARKER, blank_context, prepare_update, schema_version,
    structure_instruction, validate_context,
)


DOCUMENT = """<!-- recall-context: 1 -->
# Reader restart

Scope: Reader restart investigation, not deployment operations.
Status: active

## Summary
AWS reported a reclamation problem; the triggering workload remains unknown.

## Current understanding
Reported by AWS: metadata accumulated. An upgrade without recurrence is not proof of a fix. [Evidence](https://example.test/support)

## Open questions and next actions
Ask which workload prevents reclamation; no specific culprit is established.

## Details
Read-only savepoints and subtransaction-ID-producing writes are different.

## Evidence
[Support correspondence](https://example.test/support)
"""


class ContextPolicyTests(unittest.TestCase):
    def test_generic_structure_is_shared_and_details_are_optional(self):
        for text in (DOCUMENT, DOCUMENT.replace("## Details\nRead-only savepoints and subtransaction-ID-producing writes are different.\n\n", "")):
            report = validate_context(text, require_structured=True)
            self.assertEqual(report["schema_version"], 1)
            self.assertLess(report["summary_words"], 150)
        self.assertIn("## Summary", structure_instruction("reader-restart"))
        self.assertIn("Accuracy takes precedence", validate_context(DOCUMENT)["update_policy"])

    def test_summary_budget_does_not_destroy_supporting_detail(self):
        text = DOCUMENT.replace("Read-only savepoints and subtransaction-ID-producing writes are different.", "detail " * 250)
        report = validate_context(text, require_structured=True)
        self.assertGreater(report["detail_words"], 150)
        self.assertEqual(report["text"], text)

    def test_invalid_structure_and_budgets_are_rejected_not_truncated(self):
        variants = [
            DOCUMENT.replace("## Summary", "## Overview"),
            DOCUMENT.replace("## Evidence", "## Summary"),
            DOCUMENT.replace("Scope: Reader restart investigation, not deployment operations.\n", ""),
            DOCUMENT.replace("Status: active", "Status: probably"),
            DOCUMENT.replace("AWS reported a reclamation problem; the triggering workload remains unknown.", "word " * 151),
            DOCUMENT.replace("Read-only savepoints and subtransaction-ID-producing writes are different.", "word " * 4001),
            DOCUMENT.replace("## Summary", "# Second title\n\n## Summary"),
            DOCUMENT.replace("## Evidence\n[Support correspondence](https://example.test/support)", "## Evidence\n"),
        ]
        for text in variants:
            with self.subTest(text=text[:150]), self.assertRaises(ValueError):
                validate_context(text, require_structured=True)

    def test_fenced_examples_do_not_define_sections_or_metadata(self):
        text = DOCUMENT.replace("## Details\n", "## Details\n```markdown\n# Example\n## Evidence\nScope: not the document scope\n```\n")
        validate_context(text, require_structured=True)
        legacy = "# Old notes\n\n```markdown\n" + DOCUMENT + "\n```\n"
        self.assertIsNone(schema_version(legacy))
        self.assertEqual(validate_context(legacy)["text"], legacy)

    def test_invalid_code_fence_closer_cannot_expose_hidden_sections(self):
        hidden = DOCUMENT.replace("## Current understanding", "```text\nnot closed\n```not-a-closer\n\n## Current understanding")
        with self.assertRaisesRegex(ValueError, "context sections"):
            validate_context(hidden)

    def test_inline_multiline_comments_cannot_hide_required_sections(self):
        hidden = DOCUMENT.replace("the triggering workload remains unknown.", "the triggering workload remains unknown.<!--") + "\n-->\n"
        with self.assertRaisesRegex(ValueError, "context sections"):
            validate_context(hidden)

    def test_removing_details_also_warns_when_summary_is_unchanged(self):
        updated = DOCUMENT.replace("## Details\nRead-only savepoints and subtransaction-ID-producing writes are different.\n\n", "")
        report = validate_context(updated, original=DOCUMENT)
        self.assertTrue(any("Summary did not" in warning for warning in report["warnings"]))

    def test_unknown_schema_and_marker_removal_are_rejected(self):
        for text in (DOCUMENT.replace("recall-context: 1", "recall-context: 3"), DOCUMENT.replace(SCHEMA_MARKER, "")):
            with self.assertRaises(ValueError):
                validate_context(text, original=DOCUMENT)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_context(DOCUMENT.replace("recall-context: 1", "recall-context: 3"))

    def test_legacy_is_preserved_and_not_upgraded_in_place(self):
        legacy = "# Old notes\n\nUnstructured material, including uncertain history.\n"
        self.assertEqual(validate_context(legacy)["text"], legacy)
        self.assertEqual(prepare_update(legacy, legacy + "\nNew observation.\n")["text"], legacy + "\nNew observation.\n")
        with self.assertRaisesRegex(ValueError, "not upgraded in place"):
            validate_context(DOCUMENT, original=legacy)
        with self.assertRaisesRegex(ValueError, "format marker"):
            validate_context(legacy, require_structured=True)

    def test_warnings_expose_scope_changes_and_unchanged_summary(self):
        updated = DOCUMENT.replace("not deployment operations", "including deployment operations")
        updated = updated.replace("Reported by AWS: metadata accumulated.", "Reported by AWS: metadata accumulated over several days.")
        report = validate_context(updated, original=DOCUMENT)
        self.assertTrue(any("Scope changed" in warning for warning in report["warnings"]))
        self.assertTrue(any("Summary did not" in warning for warning in report["warnings"]))

    def test_update_time_is_not_a_new_evidence_verification_time(self):
        original = DOCUMENT.replace("# Reader restart", "> Generated: `2026-10-09T00:00:00Z`\n> Historical evidence through `2026-10-09T01:00:00Z`\n# Reader restart")
        updated = original.replace("metadata accumulated", "metadata accumulated over days")
        report = prepare_update(original, updated, now="2026-10-29T00:00:00Z")
        self.assertIn("> Updated: `2026-10-29T00:00:00Z`", report["text"])
        self.assertIn("> Historical evidence through `2026-10-09T01:00:00Z`", report["text"])
        self.assertIn("> Generated: `2026-10-09T00:00:00Z`", report["text"])
        self.assertEqual(prepare_update(original, original)["text"], original)

    def test_edit_timestamp_never_rewrites_quoted_or_fenced_evidence(self):
        evidence = "\n> Updated: `2020-01-01T00:00:00Z`\n\n```text\n> Updated: `2021-01-01T00:00:00Z`\n```\n"
        original = DOCUMENT + evidence
        updated = original.replace("metadata accumulated.", "metadata accumulated over days.")
        report = prepare_update(original, updated, now="2026-10-29T00:00:00Z")
        self.assertIn("> Updated: `2026-10-29T00:00:00Z`", report["text"])
        self.assertIn(evidence, report["text"])

    def test_prelude_cannot_hide_supporting_material_outside_budgets(self):
        for insertion in ("unbounded prose " * 4001, "```text\nhidden prose\n```", "<!-- hidden long note -->"):
            text = DOCUMENT.replace("## Summary", insertion + "\n\n## Summary")
            with self.assertRaisesRegex(ValueError, "unexpected content"):
                validate_context(text)
        with self.assertRaisesRegex(ValueError, "supporting sections exceed"):
            validate_context(DOCUMENT.replace(SCHEMA_MARKER, SCHEMA_MARKER + "\n> Repository evidence: " + "word " * 4001))

    def test_temporary_model_prompt_is_not_a_durable_evidence_reference(self):
        with self.assertRaisesRegex(ValueError, "temporary generation prompt"):
            validate_context(DOCUMENT + "\nSource: /tmp/recall-context-private.md\n")

    def test_missing_direct_evidence_is_visible_not_fabricated(self):
        report = validate_context(DOCUMENT.replace("[Support correspondence](https://example.test/support)", "Direct link not supplied."))
        self.assertTrue(any("no direct source links" in warning for warning in report["warnings"]))
        self.assertIn("Direct link not supplied", report["text"])

    def test_shipped_synthetic_example_satisfies_the_contract(self):
        example = Path(__file__).resolve().parents[1] / "docs/examples/structured-context.md"
        report = validate_context(example.read_text(encoding="utf-8"), require_structured=True)
        self.assertEqual(report["warnings"], [])
        self.assertIn("fictional example", report["text"].lower())

    def test_blank_creation_is_structured_without_inventing_facts(self):
        report = validate_context(blank_context("new-context"), require_structured=True)
        self.assertIn("Scope: Not specified.", report["text"])
        self.assertIn("No evidence recorded.", report["text"])


class ContextPolicyWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.contexts = Path(self.tmp.name) / "contexts"
        self.history = Path(self.tmp.name) / "history"
        self.contexts.mkdir()
        for key, value in (("CONTEXTS_DIR", self.contexts), ("CONTEXT_HISTORY_DIR", self.history)):
            patcher = mock.patch.object(recall, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_one_update_saves_summary_details_and_backup_together(self):
        path = self.contexts / "reader.md"
        path.write_text(DOCUMENT)
        edits = [
            {"old_text": "the triggering workload remains unknown.", "new_text": "the workload is still being investigated."},
            {"old_text": "metadata accumulated.", "new_text": "metadata accumulated over days."},
        ]
        updated = recall._apply_context_patch(DOCUMENT, edits)
        recall._write_context_update("reader", DOCUMENT, updated)
        self.assertIn("still being investigated", path.read_text())
        self.assertIn("accumulated over days", path.read_text())
        self.assertEqual((self.history / "reader.md").read_text(), DOCUMENT)
        recall._context_undo("reader")
        self.assertEqual(path.read_text(), DOCUMENT)

    def test_invalid_update_or_editor_never_writes(self):
        path = self.contexts / "reader.md"
        path.write_text(DOCUMENT)
        invalid = DOCUMENT.replace("## Summary", "## Removed")
        with self.assertRaises(recall.ContextError):
            recall._write_context_update("reader", DOCUMENT, invalid)
        with mock.patch.object(recall, "_edit_proposed_context", return_value=invalid), self.assertRaises(recall.ContextError):
            recall._context_edit("reader")
        self.assertEqual(path.read_text(), DOCUMENT)
        self.assertFalse(self.history.exists())

    def test_force_creation_does_not_migrate_legacy(self):
        path = self.contexts / "old.md"
        original = "# Original\n\nKeep this exact old document.\n"
        path.write_text(original)
        with self.assertRaisesRegex(recall.ContextError, "not upgraded in place"):
            recall._context_create("old", force=True)
        self.assertEqual(path.read_text(), original)

    def test_force_generation_does_not_overwrite_a_context_changed_during_review(self):
        path = self.contexts / "reader.md"
        path.write_text(DOCUMENT)
        original = path.read_text()
        concurrent = "# Legacy copy introduced during review\n"
        path.write_text(concurrent)
        with self.assertRaisesRegex(recall.ContextError, "changed while"):
            recall._save_created_context(path, blank_context("reader"), original, force=True)
        self.assertEqual(path.read_text(), concurrent)

    def test_json_validation_protocol_never_indexes_or_saves(self):
        payload = Path(self.tmp.name) / "proposal.json"
        payload.write_text(json.dumps({"text": DOCUMENT, "require_structured": True}))
        output = io.StringIO()
        with mock.patch.object(recall, "connect") as connect, mock.patch.object(recall, "index_all") as index, redirect_stdout(output):
            recall.main(["context", "validate", "--input-file", str(payload)])
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)
        connect.assert_not_called()
        index.assert_not_called()
        self.assertEqual(list(self.contexts.iterdir()), [])

    def test_failed_repairs_never_save_a_legacy_shaped_new_context(self):
        with mock.patch.object(recall, "_run_pi_generation", return_value="# Incorrect\n\nUnstructured."), self.assertRaisesRegex(recall.ContextError, "format marker"):
            recall._generate_creation_draft("reader", "Investigation", "custom", ["Historical evidence"], None)
        self.assertEqual(list(self.contexts.iterdir()), [])
