import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import claude_search as recall


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
        recall.CONTEXTS_DIR = Path(self.tmp.name) / "contexts"

    def tearDown(self):
        recall.CONTEXTS_DIR = self.old_contexts_dir
        self.tmp.cleanup()

    def test_create_uses_template_and_refuses_overwrite(self):
        path = recall._context_create("events-db")

        self.assertEqual(path, recall.CONTEXTS_DIR / "events-db.md")
        self.assertIn("# Events Db", path.read_text(encoding="utf-8"))
        self.assertIn("## Decisions", path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(recall.ContextError, "already exists"):
            recall._context_create("events-db")

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


if __name__ == "__main__":
    unittest.main()

