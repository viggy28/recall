import unittest
from unittest import mock

from scripts.ci.retrieval_scope import changed_paths, decide, is_known_safe, valid_path


class RetrievalScopeTests(unittest.TestCase):
    def test_all_known_safe_changes_skip(self):
        run, reason = decide(["README.md", "docs/usage.md", "docs/demo.gif"])
        self.assertFalse(run)
        self.assertIn("known-safe", reason)

    def test_unknown_or_source_change_runs(self):
        self.assertTrue(decide(["docs/usage.md", "recall_core/new_ranker.py"])[0])
        self.assertTrue(decide(["new-directory/unknown.file"])[0])

    def test_sensitive_paths_override_safe_extensions(self):
        self.assertTrue(is_known_safe("notes.md"))
        self.assertTrue(is_known_safe("README.md"))
        self.assertFalse(is_known_safe("tests/README.md"))
        self.assertFalse(is_known_safe("benchmarks/retrieval/notes.md"))
        self.assertFalse(is_known_safe(".github/workflows/retrieval-ci.yml"))
        self.assertFalse(is_known_safe("scripts/ci/policy.md"))

    def test_force_and_errors_fail_safe_to_run(self):
        self.assertTrue(decide(["README.md"], force=True)[0])
        self.assertTrue(decide([], error="bad SHA")[0])
        self.assertTrue(decide([])[0])

    def test_rename_classifies_both_old_and_new_paths(self):
        raw = b"R100\0docs/old.md\0recall_core/new.py\0A\0docs/added.md\0D\0docs/deleted.md\0"
        with mock.patch("scripts.ci.retrieval_scope.subprocess.check_output", return_value=raw):
            paths = changed_paths("base", "head")
        self.assertEqual(paths, ["docs/old.md", "recall_core/new.py", "docs/added.md", "docs/deleted.md"])
        self.assertTrue(decide(paths)[0])

    def test_malformed_paths_are_not_safe(self):
        for path in ("/absolute.md", "../escape.md", "docs/../escape.md", "docs\\file.md"):
            self.assertFalse(valid_path(path))
            self.assertFalse(is_known_safe(path))
