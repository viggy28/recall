import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from recall_core.context_creation import (
    SOURCE_MAX_EVIDENCE_CHARS, SOURCE_MAX_PATH_BYTES, collect_source_snapshot,
    lexical_source_path, source_disclosure, source_generation_prompt,
)


class SourceCollectionTests(unittest.TestCase):
    def test_collects_text_and_skips_secret_named_binary_and_dependency_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Project\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\x00\x01")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "dep.js").write_text("ignored", encoding="utf-8")

            snapshot = collect_source_snapshot(root)

            self.assertEqual([f["path"] for f in snapshot["files"]], ["README.md"])
            self.assertNotIn("TOKEN=secret", str(snapshot))

    def test_skips_high_confidence_secret_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.txt").write_text("api_key = 'abcdefghijklmnop1234'", encoding="utf-8")
            snapshot = collect_source_snapshot(root)
            self.assertEqual(snapshot["files"], [])
            self.assertIn("secret-content", {item["reason"] for item in snapshot["skipped"]})

    def test_skips_common_credentials_and_sensitive_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "id_ed25519").write_text("not-real", encoding="utf-8")
            (root / "terraform.tfstate").write_text("{}", encoding="utf-8")
            (root / ".config" / "gcloud").mkdir(parents=True)
            (root / ".config" / "gcloud" / "configurations").write_text("secret", encoding="utf-8")

            snapshot = collect_source_snapshot(root)

            self.assertEqual(snapshot["files"], [])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_does_not_follow_file_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / f"outside-{root.name}.txt"
            outside.write_text("outside secret", encoding="utf-8")
            try:
                (root / "link.txt").symlink_to(outside)
                snapshot = collect_source_snapshot(root)
                self.assertEqual(snapshot["files"], [])
            finally:
                outside.unlink(missing_ok=True)

    def test_git_mode_uses_only_tracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tracked.txt").write_text("tracked", encoding="utf-8")
            (root / "untracked.txt").write_text("untracked", encoding="utf-8")
            subprocess_result = __import__("subprocess").CompletedProcess
            with mock.patch("recall_core.context_creation.subprocess.run", side_effect=[
                subprocess_result([], 0, b"true\n", b""),
                subprocess_result([], 0, b"tracked.txt\0", b""),
            ]):
                snapshot = collect_source_snapshot(root)
            self.assertEqual([item["path"] for item in snapshot["files"]], ["tracked.txt"])

    def test_git_paths_outside_root_are_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("outside secret", encoding="utf-8")
            subprocess_result = __import__("subprocess").CompletedProcess
            with mock.patch("recall_core.context_creation.subprocess.run", side_effect=[
                subprocess_result([], 0, b"true\n", b""),
                subprocess_result([], 0, b"../outside.txt\0", b""),
            ]):
                snapshot = collect_source_snapshot(root)
            self.assertEqual(snapshot["files"], [])

    def test_enforces_file_byte_and_total_byte_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("x" * (30 * 1024), encoding="utf-8")
            for index in range(50):
                (root / f"file-{index:02d}.txt").write_text("y" * 3000, encoding="utf-8")

            snapshot = collect_source_snapshot(root)

            self.assertLessEqual(len(snapshot["files"]), 40)
            self.assertLessEqual(snapshot["bytesRead"], 80 * 1024)
            readme = next(item for item in snapshot["files"] if item["path"] == "README.md")
            self.assertTrue(readme["truncated"])
            self.assertLessEqual(readme["bytes"], 24 * 1024)

    def test_git_operational_failure_is_not_silently_treated_as_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = __import__("subprocess").CompletedProcess([], 2, b"", b"fatal: unsafe repository")
            with mock.patch("recall_core.context_creation.subprocess.run", return_value=result), \
                    self.assertRaisesRegex(ValueError, "could not inspect Git"):
                collect_source_snapshot(Path(tmp))

    def test_source_prompt_is_bounded_and_reports_omissions(self):
        snapshot = {
            "listing": ["README.md"],
            "files": [{"path": "README.md", "content": "x" * (SOURCE_MAX_EVIDENCE_CHARS * 2), "bytes": 1, "truncated": True}],
            "skipped": [{"path": ".env", "reason": "excluded"}],
        }
        prompt = source_generation_prompt("project", "Internals", snapshot)
        self.assertLessEqual(len(prompt), SOURCE_MAX_EVIDENCE_CHARS)
        self.assertIn("excluded: 1", prompt)

    def test_source_paths_are_quoted_in_disclosure_and_prompt(self):
        path = Path("/tmp/repo\nmisleading")
        disclosure = source_disclosure(path, "test/model")
        snapshot = {
            "listing": ["README\n## fake.md"],
            "files": [{"path": "README\n## fake.md", "content": "safe", "bytes": 4, "truncated": False}],
            "skipped": [],
        }

        self.assertIn('"/tmp/repo\\nmisleading"', disclosure)
        prompt = source_generation_prompt("project", "Internals", snapshot)
        self.assertIn('"README\\n## fake.md"', prompt)
        self.assertNotIn("\n## fake.md", prompt)

    def test_source_listing_respects_path_metadata_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(200):
                (root / (f"file-{index:03d}-" + "x" * 180 + ".txt")).write_text("safe", encoding="utf-8")

            snapshot = collect_source_snapshot(root)

            self.assertLessEqual(
                sum(len(path.encode("utf-8", "replace")) for path in snapshot["listing"]),
                SOURCE_MAX_PATH_BYTES,
            )

    def test_rejects_non_directory_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            path.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                collect_source_snapshot(path)

    def test_rejects_source_identity_changed_after_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "changed before collection"):
                collect_source_snapshot(root, expected_canonical=root.resolve(), expected_identity=(-1, -1))

    def test_lexical_path_does_not_require_destination_to_exist(self):
        path = lexical_source_path("missing", "/tmp")
        self.assertEqual(path, Path("/tmp/missing"))


if __name__ == "__main__":
    unittest.main()
