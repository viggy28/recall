import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from recall_core.context_creation import (
    SOURCE_MAX_EVIDENCE_CHARS, SOURCE_MAX_PATH_BYTES, collect_source_snapshot,
    lexical_source_path, load_source_snapshot, source_disclosure, source_generation_prompt,
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
        secrets = {
            "assignment.txt": "api_key = 'abcdefghijklmnop1234'",
            "dsa.txt": "-----BEGIN DSA PRIVATE KEY-----",
            "github.txt": "ghp_abcdefghijklmnopqrstuvwxyz123456",
            "aws.txt": "AKIAABCDEFGHIJKLMNOP",
            "slack.txt": "xoxb-12345678901234567890",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in secrets.items():
                (root / name).write_text(content, encoding="utf-8")
            snapshot = collect_source_snapshot(root)
            self.assertEqual(snapshot["files"], [])
            self.assertEqual(
                sum(item["reason"] == "secret-content" for item in snapshot["skipped"]),
                len(secrets),
            )

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

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW") and hasattr(os, "symlink"), "requires O_NOFOLLOW")
    def test_replacement_symlink_is_not_followed_during_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / "README.md"
            source.write_text("safe", encoding="utf-8")
            outside = root.parent / f"outside-{root.name}.txt"
            outside.write_text("outside secret", encoding="utf-8")
            real_open = os.open
            replaced = False

            def replace_then_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if Path(path) == source and not replaced:
                    replaced = True
                    source.unlink()
                    source.symlink_to(outside)
                return real_open(path, flags, *args, **kwargs)

            try:
                with mock.patch("recall_core.context_creation.os.open", side_effect=replace_then_open):
                    snapshot = collect_source_snapshot(root)
                self.assertEqual(snapshot["files"], [])
                self.assertNotIn("outside secret", str(snapshot))
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

    def test_source_prompt_rejects_impossible_evidence_instead_of_silently_truncating_it(self):
        snapshot = {
            "listing": ["README.md"],
            "files": [{"path": "README.md", "content": "x" * (SOURCE_MAX_EVIDENCE_CHARS * 2), "bytes": 1, "truncated": True}],
            "skipped": [{"path": ".env", "reason": "excluded"}],
        }
        with self.assertRaisesRegex(ValueError, "evidence exceeds"):
            source_generation_prompt("project", "Internals", snapshot)

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

    def test_loads_private_approved_snapshot_without_source_access(self):
        import json
        snapshot = {
            "listing": ["README.md"],
            "files": [{"path": "README.md", "content": "safe", "bytes": 4, "truncated": False}],
            "skipped": [], "bytesRead": 4,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            os.chmod(path, 0o600)
            loaded = load_source_snapshot(path)
        self.assertEqual(loaded["files"], snapshot["files"])

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
