import io
import json
import os
import pty
import re
import select
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import recall


class ReleaseManifestTests(unittest.TestCase):
    def test_package_versions_stay_synchronized(self):
        package_version = json.loads(Path("package.json").read_text(encoding="utf-8"))["version"]
        pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
        pyproject_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.M)

        self.assertIsNotNone(pyproject_match)
        self.assertEqual(package_version, pyproject_match.group(1))


class TuiPresentationTests(unittest.TestCase):
    def test_search_modes_use_plain_language_labels(self):
        self.assertEqual(recall._tui_mode_label("fuzzy"), "all words")
        self.assertEqual(recall._tui_mode_label("regex"), "exact pattern")
        self.assertEqual(recall._tui_mode_label("semantic"), "meaning")

    def test_search_results_reserve_a_row_for_the_matching_excerpt(self):
        self.assertEqual(recall._tui_rows_per_result(home=False), 3)
        self.assertEqual(recall._tui_rows_per_result(home=True), 2)

    def test_long_identity_text_is_bounded(self):
        lines = recall._bounded_lines("word " * 100, width=24, max_lines=2)

        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(len(line) <= 24 for line in lines))

    def test_match_preview_wraps_and_rehighlights_query(self):
        marked = f"Before {recall.HL[0]}merge{recall.HL[1]} after " + "context " * 20

        lines = recall._preview_lines(marked, "merge", "fuzzy", width=32, max_lines=4)

        self.assertGreater(len(lines), 1)
        self.assertLessEqual(len(lines), 4)
        self.assertIn(recall.HL[0] + "merge" + recall.HL[1], " ".join(lines))
        self.assertTrue(all(len(line.replace(recall.HL[0], "").replace(recall.HL[1], "")) <= 32
                            for line in lines))

    def test_tui_clipping_counts_wide_characters_as_two_cells(self):
        self.assertEqual(recall._tui_cell_width("ab〉c"), 5)
        self.assertEqual(recall._tui_clip_cells("ab〉c", 4), "ab〉")
        self.assertEqual(recall._tui_clip_cells("ab〉c", 3), "ab")

    def test_detail_pane_reserves_the_terminal_last_column(self):
        list_width, detail_x, detail_width = recall._tui_split_widths(200)

        self.assertEqual(list_width, 58)
        self.assertEqual(detail_x, 60)
        self.assertEqual(detail_x + detail_width, 199)


class SessionScopedFuzzySearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        recall.init_db(self.conn)
        rows = [
            ("/same", "same-message", "pi", "/tmp", "user", "user", "2026-01-01", 1, 1,
             "merge atlantis checkpoint together", "merge atlantis checkpoint together"),
            ("/split", "split-session", "pi", "/tmp", "user", "user", "2026-01-02", 2, 1,
             "merge workflow", "merge workflow"),
            ("/split", "split-session", "pi", "/tmp", "assistant", "assistant", "2026-01-02", 3, 2,
             "atlantis deployment", "atlantis deployment"),
            ("/split", "split-session", "pi", "/tmp", "user", "user", "2026-01-02", 4, 3,
             "checkpoint row", "checkpoint row"),
            ("/partial", "partial-session", "pi", "/tmp", "user", "user", "2026-01-03", 5, 1,
             "merge atlantis only", "merge atlantis only"),
        ]
        self.conn.executemany(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
        self.conn.commit()

    def _args(self, query):
        return SimpleNamespace(query=query, typo=False, project=None, source=None,
                               role=None, since=None, until=None, limit=10)

    def test_terms_split_across_messages_match_at_session_scope(self):
        rows = recall.search_fuzzy(self.conn, self._args("merge atlantis checkpoint"))
        sessions = {row["session_id"] for row in rows}

        self.assertIn("split-session", sessions)
        self.assertNotIn("partial-session", sessions)

    def test_same_message_match_ranks_before_scattered_match(self):
        rows = recall.search_fuzzy(self.conn, self._args("merge atlantis checkpoint"))
        first_seen = list(dict.fromkeys(row["session_id"] for row in rows))

        self.assertEqual(first_seen[:2], ["same-message", "split-session"])


class IntegrationJsonTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        recall.init_db(self.conn)
        self.conn.execute(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("/tmp/pi-session.jsonl", "abcd-1234", "pi", "/tmp", "user", "user",
             "2026-01-01T00:00:00Z", 1, 1, "extension search", "extension search"),
        )
        self.conn.execute(
            "INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("/tmp/pi-session.jsonl", "abcd-1234", "pi", "/tmp", 1, 1, 1, 1, 1, 1),
        )
        self.conn.commit()

    def test_search_json_contains_extension_navigation_fields(self):
        args = SimpleNamespace(
            query="extension", regex=False, semantic=False, typo=False, project=None,
            source=None, role=None, since=None, until=None, limit=10, json=True,
        )
        rows = recall.search_fuzzy(self.conn, args)
        out = io.StringIO()

        with redirect_stdout(out):
            recall.render(self.conn, [dict(row) for row in rows], args)

        result = __import__("json").loads(out.getvalue())[0]
        self.assertEqual(result["title"], "extension search")
        self.assertEqual(result["resume_arg"], "/tmp/pi-session.jsonl")

    def test_recent_json_contains_stable_pi_resume_argument(self):
        out = io.StringIO()

        with redirect_stdout(out):
            recall.render_recent_json(self.conn, limit=1)

        result = __import__("json").loads(out.getvalue())[0]
        self.assertEqual(result["session_id"], "abcd-1234")
        self.assertEqual(result["resume_arg"], "/tmp/pi-session.jsonl")


class ContextBankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_contexts_dir = recall.CONTEXTS_DIR
        self.old_context_history_dir = recall.CONTEXT_HISTORY_DIR
        recall.CONTEXTS_DIR = Path(self.tmp.name) / "contexts"
        recall.CONTEXT_HISTORY_DIR = Path(self.tmp.name) / "context-history"

    def tearDown(self):
        recall.CONTEXTS_DIR = self.old_contexts_dir
        recall.CONTEXT_HISTORY_DIR = self.old_context_history_dir
        self.tmp.cleanup()

    def test_create_uses_template_and_refuses_overwrite(self):
        path = recall._context_create("events-db")

        self.assertEqual(path, recall.CONTEXTS_DIR / "events-db.md")
        self.assertIn("# Events Db", path.read_text(encoding="utf-8"))
        self.assertIn("## Decisions", path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(recall.ContextError, "already exists"):
            recall._context_create("events-db")

    def test_natural_create_is_one_reviewed_operation(self):
        response = """# Project\n\n## Current state\n\n- Ready.\n\n## Decisions\n\n## Constraints\n\n## Open questions\n\n## References"""
        args = SimpleNamespace(name="project", instruction="Track project readiness.",
                               instruction_file=None, model=None, blank=False,
                               dry_run=False, yes=True, force=False)
        out = io.StringIO()

        with mock.patch.object(recall, "_run_pi_generation", return_value=response), redirect_stdout(out):
            path = recall._context_create_natural(args)

        self.assertIn("Generating a context draft", out.getvalue())
        self.assertIn("- Ready.", path.read_text(encoding="utf-8"))

    def test_natural_create_dry_run_does_not_write(self):
        response = """# Project\n## Current state\n## Decisions\n## Constraints\n## Open questions\n## References"""
        args = SimpleNamespace(name="project", instruction="Track readiness.",
                               instruction_file=None, model=None, blank=False,
                               dry_run=True, yes=False, force=False)

        with mock.patch.object(recall, "_run_pi_generation", return_value=response):
            self.assertIsNone(recall._context_create_natural(args))

        self.assertFalse(recall._context_path("project").exists())

    def test_context_name_rejects_paths_and_invalid_names(self):
        for name in ("../events", "Events", "events_db", "-events", "events-", "a" * 65):
            with self.subTest(name=name), self.assertRaises(recall.ContextError):
                recall._context_path(name)

    def test_import_and_export_round_trip(self):
        source = Path(self.tmp.name) / "handoff.md"
        source.write_text("# Handoff\n\nDecision: keep Markdown.\n", encoding="utf-8")
        imported = recall._context_import(str(source), name="events-db")
        destination = Path(self.tmp.name) / "exported.md"

        exported = recall._context_export("events-db", str(destination))

        self.assertEqual(imported.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        self.assertEqual(exported.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(recall.ContextError, "already exists"):
            recall._context_export("events-db", str(destination))

    def test_force_delete_removes_context(self):
        path = recall._context_create("events-db")

        self.assertTrue(recall._context_delete("events-db", force=True))
        self.assertFalse(path.exists())

    def test_context_path_and_name_shorthand(self):
        path = recall._context_create("events-db")

        out = io.StringIO()
        with redirect_stdout(out):
            recall.main(["context", "path", "events-db"])
        self.assertEqual(out.getvalue().strip(), str(path))

        out = io.StringIO()
        with redirect_stdout(out):
            recall.main(["context", "events-db"])
        self.assertEqual(out.getvalue(), path.read_text(encoding="utf-8"))

    def test_context_patch_updates_multiple_unique_statements(self):
        original = "# Project\n\n## Current state\n\n- Migration is planned.\n\n## Open questions\n\n- Is it ready?\n"
        response = __import__("json").dumps({"edits": [
            {"old_text": "- Migration is planned.", "new_text": "- Migration is complete."},
            {"old_text": "- Is it ready?\n", "new_text": ""},
        ]})

        edits = recall._parse_context_patch(response)
        updated = recall._apply_context_patch(original, edits)

        self.assertIn("Migration is complete", updated)
        self.assertNotIn("Migration is planned", updated)
        self.assertNotIn("Is it ready?", updated)
        diff = recall._context_diff("project", original, updated, color=False)
        self.assertIn("-- Migration is planned", diff)
        self.assertIn("+- Migration is complete", diff)
        self.assertNotIn(" ## Current state", diff)

    def test_context_diff_uses_pr_style_colors_on_a_terminal(self):
        original = "# Project\n\n- Planned.\n"
        updated = "# Project\n\n- Complete.\n"

        diff = recall._context_diff("project", original, updated, color=True)

        self.assertIn("\033[1m--- project (current)", diff)
        self.assertIn("\033[36m@@", diff)
        self.assertIn("\033[31m-- Planned.", diff)
        self.assertIn("\033[32m+- Complete.", diff)

    def test_context_patch_rejects_malformed_model_output(self):
        for response in ("not json", "{}", '{"edits":[]}', '{"edits":[{"old_text":"","new_text":"x"}]}'):
            with self.subTest(response=response), self.assertRaises(recall.ContextError):
                recall._parse_context_patch(response)

    def test_context_patch_rejects_ambiguous_and_overlapping_edits(self):
        with self.assertRaisesRegex(recall.ContextError, "exactly once"):
            recall._apply_context_patch("same same", [{"old_text": "same", "new_text": "new"}])
        with self.assertRaisesRegex(recall.ContextError, "overlapping"):
            recall._apply_context_patch("abcdef", [
                {"old_text": "abc", "new_text": "x"},
                {"old_text": "bcde", "new_text": "y"},
            ])

    def test_context_update_writes_verified_backup_and_undo(self):
        path = recall._context_create("project")
        original = path.read_text(encoding="utf-8")
        updated = original.replace("## Decisions", "## Decisions\n\n- Done.")

        written = recall._write_context_update("project", original, updated)

        self.assertEqual(written.read_text(encoding="utf-8"), updated)
        self.assertEqual(recall._context_backup_path("project").read_text(encoding="utf-8"), original)
        recall._context_undo("project")
        self.assertEqual(written.read_text(encoding="utf-8"), original)
        self.assertEqual(recall._context_backup_path("project").read_text(encoding="utf-8"), updated)

    def test_context_update_detects_concurrent_change(self):
        path = recall._context_create("project")
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "\nConcurrent change.\n", encoding="utf-8")

        with self.assertRaisesRegex(recall.ContextError, "changed while"):
            recall._write_context_update("project", original, original + "\nProposed.\n")

    @unittest.skipUnless(os.name == "posix", "requires a pseudo-terminal")
    def test_context_update_prompt_supports_shell_cursor_controls(self):
        script = (
            "import recall; "
            "print(input('Describe what changed: '), flush=True)"
        )
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(sys.executable, [sys.executable, "-c", script])
        output = bytearray()
        try:
            while b"Describe what changed: " not in output:
                ready, _, _ = select.select([fd], [], [], 2)
                self.assertTrue(ready, "prompt did not appear")
                output.extend(os.read(fd, 1024))
            # Exercise Option/Alt-B word movement, Home, and Ctrl-A.
            os.write(fd, b"hello world\x1bbnew \x1b[HStart \x01First \r")
            while b"First Start hello new world" not in output:
                ready, _, _ = select.select([fd], [], [], 2)
                self.assertTrue(ready, "prompt did not finish")
                output.extend(os.read(fd, 1024))
        finally:
            _, status = os.waitpid(pid, 0)
            os.close(fd)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertIn(b"First Start hello new world", output)
        self.assertNotIn(b"^A", output)
        self.assertNotIn(b"^[", output)

    def test_context_update_cli_is_one_operation(self):
        path = recall._context_create("project")
        original = path.read_text(encoding="utf-8")
        response = __import__("json").dumps({"edits": [{
            "old_text": "## Open questions",
            "new_text": "## Open questions\n\n- None.",
        }]})
        out = io.StringIO()

        with mock.patch.object(recall, "_run_pi_generation", return_value=response), redirect_stdout(out):
            recall.main(["context", "update", "project", "No open questions remain.", "--yes"])

        self.assertIn("Generating a proposed update", out.getvalue())
        self.assertIn("updated and verified", out.getvalue())
        self.assertIn(f"previous revision: {recall._context_backup_path('project')}", out.getvalue())
        self.assertIn("undo: recall context undo project", out.getvalue())
        self.assertIn("- None.", path.read_text(encoding="utf-8"))
        self.assertEqual(recall._context_backup_path("project").read_text(encoding="utf-8"), original)

    def test_context_update_cancel_never_writes(self):
        path = recall._context_create("project")
        original = path.read_text(encoding="utf-8")
        response = '{"edits":[{"old_text":"## Decisions","new_text":"## Decisions\\n\\n- Done."}]}'
        args = SimpleNamespace(name="project", instruction="Done.", instruction_file=None,
                               replace=None, model=None, dry_run=False, yes=False)

        with mock.patch.object(recall, "_run_pi_generation", return_value=response), \
                mock.patch.object(recall.sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="cancel"):
            self.assertIsNone(recall._context_update(args))

        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(recall._context_backup_path("project").exists())

    def test_context_update_supports_model_free_replacement(self):
        path = recall._context_create("project")
        args = SimpleNamespace(name="project", instruction=None, instruction_file=None,
                               replace=[["## Decisions", "## Decisions\n\n- Done."]],
                               model=None, dry_run=False, yes=True)

        with mock.patch.object(recall, "_run_pi_generation") as generate:
            recall._context_update(args)

        generate.assert_not_called()
        self.assertIn("- Done.", path.read_text(encoding="utf-8"))

    def test_context_update_dry_run_never_writes(self):
        path = recall._context_create("project")
        original = path.read_text(encoding="utf-8")
        response = '{"edits":[{"old_text":"## Decisions","new_text":"## Decisions\\n\\n- Done."}]}'
        out = io.StringIO()

        with mock.patch.object(recall, "_run_pi_generation", return_value=response), redirect_stdout(out):
            recall.main(["context", "update", "project", "Done.", "--dry-run"])

        self.assertIn("project (proposed)", out.getvalue())
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(recall._context_backup_path("project").exists())

    def _generation_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        recall.init_db(conn)
        conn.executemany(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("/tmp/session.jsonl", "abcd-1234", "pi", "/tmp/project", "user", "user",
                 "2026-07-01T00:00:00Z", 1, 1, "Choose Markdown", "Choose Markdown"),
                ("/tmp/session.jsonl", "abcd-1234", "pi", "/tmp/project", "assistant", "assistant",
                 "2026-07-01T00:01:00Z", 2, 2, "Markdown is portable", "Markdown is portable"),
            ],
        )
        conn.commit()
        return conn

    def test_generation_uses_latest_claude_compaction_and_tail(self):
        conn = self._generation_db()
        conn.execute("UPDATE messages SET source='claude-code'")
        summary = recall._COMPACTION_PREFIX + "\n\nDecision: use the latest design."
        conn.executemany(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("/tmp/session.jsonl", "abcd-1234", "claude-code", "/tmp/project", "user", "user",
                 "2026-07-01T00:02:00Z", 3, 3, summary, summary),
                ("/tmp/session.jsonl", "abcd-1234", "claude-code", "/tmp/project", "assistant", "assistant",
                 "2026-07-01T00:03:00Z", 4, 4, "Tail update", "Tail update"),
            ],
        )
        conn.commit()
        session = recall._resolve_context_session(conn, "abcd")

        text, compacted = recall._session_generation_text(conn, session)

        self.assertTrue(compacted)
        self.assertIn("Decision: use the latest design", text)
        self.assertIn("Tail update", text)
        self.assertNotIn("Choose Markdown", text)

    def test_generate_context_with_pi_and_deterministic_source(self):
        conn = self._generation_db()
        args = SimpleNamespace(
            name="events-db", session=["abcd"], result=None, model="test/model",
            dry_run=False, yes=True, force=False,
        )

        with mock.patch.object(
                recall, "_run_pi_generation", return_value="# Events DB\n\n## Decisions\n\nUse Markdown."
        ) as generate:
            path = recall._context_generate(conn, args)

        text = path.read_text(encoding="utf-8")
        self.assertIn("Generated by recall", text)
        self.assertIn("> Generated: `", text)
        self.assertIn("> Historical source coverage through: `1970-01-01T00:00:02+00:00`", text)
        self.assertIn("Use Markdown", text)
        self.assertIn("`abcd-1234` (pi; last active `1970-01-01T00:00:02+00:00`)", text)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1], "test/model")

    def test_chunked_generation_reports_final_synthesis(self):
        conn = self._generation_db()
        conn.execute(
            "UPDATE messages SET nl_text=?, text=? WHERE role='assistant'",
            ("x" * (recall.GENERATION_CHUNK_CHARS + 1),) * 2,
        )
        conn.commit()
        args = SimpleNamespace(
            name="events-db", session=["abcd"], result=None, model=None,
            dry_run=False, yes=True, force=False,
        )
        stderr = io.StringIO()

        with mock.patch.object(
                recall, "_run_pi_generation", return_value="# Context\n\n## Current state\n\nReady."
        ) as generate, redirect_stderr(stderr):
            recall._context_generate(conn, args)

        self.assertGreaterEqual(generate.call_count, 3)
        self.assertIn("combining 2 summaries...", stderr.getvalue())

    def test_generate_dry_run_does_not_call_model_or_write(self):
        conn = self._generation_db()
        args = SimpleNamespace(
            name="events-db", session=["abcd"], result=None, model=None,
            dry_run=True, yes=False, force=False,
        )

        with mock.patch.object(recall, "_run_pi_generation") as generate:
            self.assertIsNone(recall._context_generate(conn, args))

        generate.assert_not_called()
        self.assertFalse(recall._context_path("events-db").exists())

    def test_pi_generation_is_ephemeral_tool_free_and_supports_model(self):
        prompt_path = None

        def fake_run(argv, **kwargs):
            nonlocal prompt_path
            prompt_path = Path(argv[-1][1:])
            self.assertEqual(prompt_path.read_text(encoding="utf-8"), "summarize this")
            return SimpleNamespace(returncode=0, stdout="# Summary\n", stderr="")

        with mock.patch.object(recall.subprocess, "run", side_effect=fake_run) as run:
            output = recall._run_pi_generation("summarize this", "anthropic/test")

        self.assertEqual(output, "# Summary")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["pi", "--print", "--no-session", "--no-tools"])
        self.assertIn("--model", argv)
        self.assertFalse(prompt_path.exists())


class CoreModuleBoundaryTests(unittest.TestCase):
    def test_public_facade_exports_ingestion_types(self):
        from recall_core import ingestion

        self.assertIs(recall.Source, ingestion.Source)
        self.assertIs(recall.ClaudeCodeSource, ingestion.ClaudeCodeSource)
        self.assertIs(recall.PiSource, ingestion.PiSource)
        self.assertIs(recall.CodexSource, ingestion.CodexSource)

    def test_public_facade_exports_semantic_config(self):
        from recall_core import ingestion

        self.assertEqual(recall.EMBED_MODEL, ingestion.EMBED_MODEL)
        self.assertEqual(recall.CHUNK_MAX, ingestion.CHUNK_MAX)
        self.assertEqual(recall.CHUNK_TARGET, ingestion.CHUNK_TARGET)
        self.assertGreater(len(list(recall._chunk("x" * (ingestion.CHUNK_MAX + 1)))), 1)

    def test_public_facade_exports_index_and_retrieval_functions(self):
        from recall_core import indexing, retrieval

        self.assertIs(recall.connect, indexing.connect)
        self.assertIs(recall.init_db, indexing.init_db)
        self.assertIs(recall.index, indexing.index)
        self.assertIs(recall.search_fuzzy, retrieval.search_fuzzy)
        self.assertIs(recall.search_regex, retrieval.search_regex)

if __name__ == "__main__":
    unittest.main()
