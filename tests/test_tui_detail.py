"""Bounded recognition cards and opt-in previews of fictional transcripts."""
import curses
import re
import sqlite3
import unittest
from types import SimpleNamespace
from unittest import mock

import recall


class TuiDetailFixture:
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("regexp", 2, lambda p, s: bool(s and re.search(p, s)))
        recall.init_db(self.conn)
        self.first = self.add("Original request unrelated to the match", "user", 1)
        self.before = self.add("Here are the review findings.", "assistant", 2)
        self.match = self.add("Explain the PR using the skill", "user", 3)
        self.tool = self.add("tool noise", "tool", 4, natural="")
        self.after = self.add("The explanation is ready.\n\nIt covers the index change.", "assistant", 5)
        self.add("What changed after that?", "user", 6)
        self.latest = self.add("Latest answer: tests now cover concurrent writes.", "assistant", 7)
        self.entry = dict(
            n=1, session_id="session-one", source="pi", title="Original request unrelated to the match",
            project="/tmp/fictional/project", snip="Explain the PR using the skill",
            detail_snip="Explain the PR using the skill", match_id=self.match,
            resume_status="ok", resume_path="/tmp", resume_arg="/session.jsonl", hits=1,
            sim=None, ts="2026-01-01T00:03:00Z",
        )

    def tearDown(self):
        self.conn.close()

    def add(self, text, role, line, *, natural=None, path="/session.jsonl", source="pi", sid="session-one", epoch=None):
        return self.conn.execute(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (path, sid, source, "/tmp/fictional/project", role, role if role != "tool" else "toolResult",
             "2026-01-01", line * 60 if epoch is None else epoch, line, text,
             text if natural is None else natural),
        ).lastrowid

    def detail(self, match_id=None):
        return recall._session_detail(self.conn, "session-one", "pi", self.match if match_id is None else match_id)

    def rows(self, *, home=False, mode="fuzzy", width=56, entry=None, detail=None):
        return recall._tui_detail_rows(entry or self.entry, detail or self.detail(), width, home,
                                       "explain skill", mode)


class TuiDetailTests(TuiDetailFixture, unittest.TestCase):
    def test_neighbors_skip_tools_and_use_the_same_transcript_file(self):
        self.add("Subagent request", "user", 2, path="/subagents/agent.jsonl", epoch=179)
        self.add("Subagent response", "assistant", 4, path="/subagents/agent.jsonl", epoch=181)
        detail = self.detail()
        self.assertEqual(detail["before"]["id"], self.before)
        self.assertEqual(detail["match"]["id"], self.match)
        self.assertEqual(detail["after"]["id"], self.after)
        self.assertEqual(detail["latest_assistant"]["id"], self.latest)

    def test_source_scopes_all_detail_queries(self):
        self.add("Other harness answer", "assistant", 90, source="claude-code")
        detail = self.detail()
        self.assertEqual(detail["count"], 6)
        self.assertEqual(detail["latest_assistant"]["id"], self.latest)
        self.assertIsNone(recall._session_detail(self.conn, "session-one", "claude-code", self.match)["match"])

    def test_latest_exchange_skips_injected_text_and_uses_id_to_break_timestamp_ties(self):
        self.add("<skill>injected attachment</skill>", "user", 8)
        self.add(recall._COMPACTION_PREFIX + " boilerplate", "user", 9)
        self.add("model thinking and tool call", "assistant", 10, natural="")
        newest = self.add("Final conversational reply", "assistant", 11, epoch=420)
        detail = self.detail()
        self.assertEqual(detail["latest_user"]["text"], "What changed after that?")
        self.assertEqual(detail["latest_assistant"]["id"], newest)

    def test_search_shows_context_and_latest_answer_without_repeating_first_prompt(self):
        header, body, _ = self.rows()
        text = "\n".join(row[1] for row in body)
        self.assertIn("Conversation around match", text)
        self.assertIn("Before · Assistant", text)
        self.assertIn("Match · User", text)
        self.assertIn("After · Assistant", text)
        self.assertIn("The explanation is ready.", text)
        self.assertIn("Latest assistant message", text)
        self.assertIn("concurrent writes", text)
        self.assertNotIn("First prompt", text)
        self.assertNotIn("Original request", text)
        self.assertIn("session-one", text)
        self.assertIn(recall.HL[0] + "Explain" + recall.HL[1], text)
        self.assertTrue(any("6 messages" in row[1] for row in header))
        self.assertTrue(any("/tmp/fictional/project" in row[1] for row in header))

    def test_latest_message_is_not_duplicated_when_it_is_already_in_context(self):
        header, body, _ = self.rows(detail=self.detail(self.latest))
        text = "\n".join(row[1] for row in body)
        self.assertEqual(text.count("Latest answer:"), 1)
        self.assertNotIn("Latest assistant message", text)

    def test_recent_view_shows_latest_request_and_answer(self):
        _, body, _ = self.rows(home=True)
        text = "\n".join(row[1] for row in body)
        self.assertIn("Latest request", text)
        self.assertIn("What changed after that?", text)
        self.assertIn("Latest assistant message", text)
        self.assertNotIn("Conversation around match", text)
        self.assertNotIn("Original request", text)

    def test_full_match_and_reply_fill_a_tall_pane_and_remain_scrollable(self):
        long_text = "\n".join(f"Review finding {n}: explain the skill." for n in range(90))
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (long_text, long_text, self.match))
        header, body, _ = self.rows()
        self.assertTrue(any("Review finding 89" in row[1] for row in body))
        h, visible, indicator, top, maximum, page = recall._tui_detail_viewport(header, body, 52, 0)
        self.assertEqual(len(h) + len(visible) + 1, 52)
        self.assertGreater(maximum, 0)
        self.assertIn("PgUp/PgDn", indicator)
        self.assertEqual(top, 0)
        _, bottom, _, top, _, _ = recall._tui_detail_viewport(header, body, 52, 10000)
        self.assertEqual(top, maximum)
        self.assertTrue(any("session-one" in row[1] for row in bottom))
        self.assertGreater(page, 4)

    def test_scroll_offset_is_clamped_after_resize(self):
        header, body, _ = self.rows()
        h, visible, _, offset, maximum, page = recall._tui_detail_viewport(header, body, 12, 10000)
        self.assertEqual(offset, maximum)
        self.assertLessEqual(len(h) + len(visible) + 1, 12)
        self.assertGreaterEqual(page, 1)

    def test_long_preceding_turn_is_bounded_but_match_is_not(self):
        long_text = "\n".join(f"Context line {n}" for n in range(90))
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (long_text, long_text, self.before))
        _, body, focus = self.rows()
        self.assertLess(focus, 12)
        text = "\n".join(row[1] for row in body)
        self.assertNotIn("Context line 0\n", text)
        self.assertIn("Context line 89", text)

    def test_match_deep_in_a_message_has_a_scroll_focus(self):
        text = "\n".join(["Unrelated background."] * 90 + ["Explain the PR using the skill"])
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.match))
        _, body, focus = self.rows()
        self.assertGreater(focus, 90)
        self.assertIn("Explain", body[focus][1])

    def test_semantic_focus_uses_passage_instead_of_query_word_overlap(self):
        text = "\n".join(["Unrelated background."] * 90 + ["Use a rendered walkthrough."])
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.match))
        entry = {**self.entry, "detail_snip": "Use a rendered walkthrough."}
        _, body, focus = self.rows(mode="semantic", entry=entry)
        self.assertGreater(focus, 90)
        self.assertIn("rendered walkthrough", body[focus][1])

    def test_tool_only_match_remains_visible_and_labeled(self):
        _, body, _ = self.rows(detail=self.detail(self.tool))
        text = "\n".join(row[1] for row in body)
        self.assertIn("Match · Tool output", text)
        self.assertIn("tool noise", text)

    def test_regex_match_inside_mixed_tool_and_prose_content_remains_visible(self):
        raw = "Conversational prose\n[tool: bash] explain skill"
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (raw, "Conversational prose", self.match))
        _, body, _ = self.rows(mode="regex")
        text = "\n".join(row[1] for row in body)
        self.assertIn("tool or metadata", text)
        self.assertIn("[tool: bash]", text)

    def test_fuzzy_match_inside_mixed_tool_and_prose_content_remains_visible(self):
        raw = "Conversational prose\n[tool: bash] explain skill"
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (raw, "Conversational prose", self.match))
        _, body, focus = self.rows()
        text = "\n".join(row[1] for row in body)
        self.assertIn("tool or metadata", text)
        self.assertIn("[tool: bash]", text)
        self.assertIn("explain", body[focus][1])

    def test_semantic_preview_highlights_literal_words_when_present(self):
        _, body, _ = self.rows(mode="semantic")
        text = "\n".join(row[1] for row in body)
        self.assertIn(recall.HL[0] + "Explain" + recall.HL[1], text)
        self.assertIn(recall.HL[0] + "skill" + recall.HL[1], text)

    def test_missing_match_falls_back_to_stored_passage(self):
        self.conn.execute("DELETE FROM messages WHERE id=?", (self.match,))
        _, body, _ = self.rows()
        self.assertIn("Explain", "\n".join(row[1] for row in body))

    def test_neighbor_order_uses_transcript_lines_when_timestamps_are_missing(self):
        self.conn.execute("UPDATE messages SET epoch=NULL")
        detail = self.detail()
        self.assertEqual(detail["before"]["id"], self.before)
        self.assertEqual(detail["after"]["id"], self.after)

    def test_fuzzy_and_regex_results_keep_the_actual_message_id(self):
        args = SimpleNamespace(query="explain skill", regex=False, semantic=False, typo=False,
                               limit=10, project=None, source=None, role=None, since=None, until=None)
        for regex in (False, True):
            args.regex = regex
            args.query = "Explain.*skill" if regex else "explain skill"
            rows = (recall.search_regex if regex else recall.search_fuzzy)(self.conn, args)
            results, _ = recall._build_results(self.conn, [dict(r) for r in rows], args)
            self.assertEqual(results[0]["match_id"], self.match)

    def test_fuzzy_preview_id_tracks_conversational_hit_not_tool_representative(self):
        args = SimpleNamespace(query="explain skill", regex=False, semantic=False, limit=10)
        base = dict(session_id="session-one", source="pi", path="/session.jsonl", project="/tmp",
                    ts="2026-01-01", type="user", present=1, snip="explain skill", hit_count=2)
        rows = [dict(base, id=self.tool, score=-5, nl_text=""),
                dict(base, id=self.match, score=-2, nl_text="Explain the PR using the skill")]
        results, _ = recall._build_results(self.conn, rows, args)
        self.assertEqual(results[0]["match_id"], self.match)

    def test_semantic_results_keep_full_passage_and_message_id(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        passage = "Opening context. " * 15 + "The important sentence at the end."
        self.conn.execute("INSERT INTO chunks(message_id,session_id,ord,text) VALUES(?,?,?,?)",
                          (self.match, "session-one", 0, passage))
        cid = self.conn.execute("SELECT id FROM chunks").fetchone()[0]
        self.conn.execute("INSERT INTO embeddings(chunk_id,vec) VALUES(?,?)", (cid, np.array([1.0, 0.0], dtype=np.float32).tobytes()))
        model = SimpleNamespace(query_embed=lambda _: [np.array([1.0, 0.0])])
        args = SimpleNamespace(query="explain skill", regex=False, semantic=True, limit=10)
        with mock.patch.object(recall, "_load_embedder", return_value=(model, np)):
            rows = recall.search_semantic(self.conn, args, warn=False)
        results, _ = recall._build_results(self.conn, rows, args)
        self.assertEqual(results[0]["match_id"], self.match)
        self.assertTrue(results[0]["detail_snip"].endswith("important sentence at the end."))


class TuiRecognitionTests(TuiDetailFixture, unittest.TestCase):
    def card(self, *, home=False, width=80, entry=None, detail=None, query="explain skill", mode="fuzzy"):
        return recall._tui_recognition_rows(entry or self.entry, detail or self.detail(), width,
                                             home, query, mode)

    def text(self, rows):
        return "\n".join(row[1] for row in rows).replace(recall.HL[0], "").replace(recall.HL[1], "")

    def test_card_prioritizes_match_and_latest_reply_not_surrounding_transcript(self):
        header, body, focus = self.card()
        text = self.text(body)
        self.assertIn("WHY THIS MATCHED · you", text)
        self.assertIn("Explain the PR using the skill", text)
        self.assertIn("LATEST REPLY · excerpt", text)
        self.assertIn("concurrent writes", text)
        self.assertNotIn("Here are the review findings", text)
        self.assertNotIn("What changed after that?", text)
        self.assertNotIn("Conversation around match", text)
        self.assertNotIn("REQUEST", text)  # The selected user request is the match.
        self.assertIn("Ctrl+T full preview", text)
        self.assertIn("session-one", text)
        self.assertEqual(focus, 0)
        self.assertIn("6 messages", self.text(header))

    def test_short_title_match_is_highlighted_once_instead_of_repeated_as_an_excerpt(self):
        entry = {**self.entry, "title": "Explain the PR using the skill"}
        header, body, _ = self.card(entry=entry)
        text = self.text(header + body)
        self.assertEqual(text.count("Explain the PR using the skill"), 1)
        self.assertIn("MATCH · shown in title", text)
        self.assertNotIn("WHY THIS MATCHED", text)
        self.assertEqual(header[0][0], "H")
        self.assertIn(recall.HL[0] + "Explain" + recall.HL[1], header[0][1])

    def test_recent_card_bounds_a_long_markdown_answer_instead_of_filling_the_pane(self):
        text = "**Status:** latency recovered after the request rate dropped.\n\n## Details\n"
        text += "\n".join(f"Finding {i}: long investigation details." for i in range(100))
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.latest))
        header, body, _ = self.card(home=True)
        rendered = self.text(body)
        self.assertIn("Status: latency recovered", rendered)
        self.assertNotIn("**", rendered)
        self.assertNotIn("Finding 0", rendered)
        self.assertNotIn("Finding 99", rendered)
        self.assertLessEqual(len(header) + len(body), 22)

    def test_search_card_centers_excerpt_on_the_actual_match_not_opening_boilerplate(self):
        text = "Unrelated background. " * 100 + "Explain the PR using the skill."
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.match))
        _, body, _ = self.card()
        self.assertIn("Explain the PR using the skill", self.text(body))
        self.assertLessEqual(sum(kind == "H" for kind, _, _ in body), 10)

    def test_semantic_card_uses_the_matched_passage_not_the_whole_message(self):
        text = "Unrelated background. " * 100 + "Use a rendered walkthrough."
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.match))
        entry = {**self.entry, "detail_snip": "Use a rendered walkthrough."}
        _, body, _ = self.card(mode="semantic", entry=entry)
        self.assertIn("Use a rendered walkthrough.", self.text(body))
        self.assertNotIn("Unrelated background", self.text(body))

    def test_request_is_omitted_when_the_title_already_says_it(self):
        request = self.add("Find the concurrent write regression", "user", 8)
        entry = {**self.entry, "title": "Find the concurrent write regression"}
        _, body, _ = self.card(home=True, entry=entry)
        self.assertNotIn("REQUEST", self.text(body))
        self.assertNotIn("Find the concurrent write regression", self.text(body))

    def test_assistant_match_includes_nearby_request_and_is_not_repeated_as_latest_reply(self):
        detail = self.detail(self.latest)
        _, body, _ = self.card(detail=detail, entry={**self.entry, "detail_snip": "Latest answer:"})
        text = self.text(body)
        self.assertIn("WHY THIS MATCHED · assistant", text)
        self.assertIn("REQUEST", text)
        self.assertIn("What changed after that?", text)
        self.assertNotIn("LATEST REPLY", text)
        self.assertEqual(text.count("Latest answer:"), 1)

    def test_tool_matches_remain_visible_but_bounded(self):
        raw = "Conversational prose\n[tool: bash] explain skill " + "large output " * 500
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (raw, "Conversational prose", self.match))
        for mode in ("fuzzy", "regex"):
            _, body, _ = self.card(mode=mode)
            text = self.text(body)
            self.assertIn("WHY THIS MATCHED · tool content", text)
            self.assertIn("[tool: bash]", text)
            self.assertIn("explain skill", text)
            self.assertLessEqual(sum(kind == "H" for kind, _, _ in body), 10)

    def test_references_are_recognizable_deduplicated_and_capped(self):
        text = (
            "PR https://github.com/acme/identity/pull/123 and "
            "https://github.com/acme/identity/pull/123/files. "
            "Task https://linear.app/acme/issue/DB-456/index-fix "
            "Docs https://docs.example.com/index?token=secret "
            "More https://other.example.com/fourth"
        )
        references = recall._tui_card_references([text, text])
        self.assertEqual(references, ["GitHub  acme/identity #123", "Linear  DB-456", "docs.example.com/index"])
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.latest))
        _, body, _ = self.card()
        self.assertIn("REFERENCES", self.text(body))
        self.assertIn("acme/identity #123", self.text(body))
        # Reference labels don't expose credentials in query strings. The
        # original excerpt can still contain the original, literal link.
        self.assertFalse(any("secret" in label for label in references))

    def test_card_uses_semantic_styles_and_highlights_literal_words_even_in_meaning_mode(self):
        header, body, _ = self.card(mode="semantic")
        self.assertTrue(any(style == "source" for _, _, style in header))
        self.assertTrue(any(style == "dim" for _, _, style in header))
        self.assertTrue(any(style == "match_label" for _, _, style in body))
        self.assertTrue(any(style == "label" for _, _, style in body))
        self.assertIn(recall.HL[0] + "Explain" + recall.HL[1], "\n".join(row[1] for row in body))

    def test_card_has_a_readable_line_length_on_very_wide_terminals(self):
        text = "Full explanation " * 400
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (text, text, self.latest))
        header, body, _ = self.card(home=True, width=180)
        for _, line, _ in header + body:
            plain = line.replace(recall.HL[0], "").replace(recall.HL[1], "")
            self.assertLessEqual(recall._tui_cell_width(plain), 80)
        self.assertLessEqual(len(header) + len(body), 24)

    def test_empty_session_has_a_quiet_explicit_fallback(self):
        detail = recall._session_detail(self.conn, "missing-session", "pi")
        _, body, _ = self.card(home=True, detail=detail)
        self.assertIn("No conversation text indexed", self.text(body))


class TuiTextWrappingTests(unittest.TestCase):
    def test_wrap_preserves_paragraphs_and_code_indentation(self):
        lines = recall._tui_wrap_lines("Paragraph one.\n\n    code()\nParagraph two.", 40)
        self.assertEqual(lines, ["Paragraph one.", "", "    code()", "Paragraph two."])

    def test_wrap_counts_terminal_cells_and_limits_identity_lines(self):
        lines = recall._tui_wrap_lines("路径 " * 30, 13, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(recall._tui_cell_width(line) <= 13 for line in lines))
        self.assertTrue(recall._tui_wrap_lines("路", 1))

    def test_wrap_removes_terminal_controls_from_transcript(self):
        lines = recall._tui_wrap_lines("\x1b[31mred\x1b[0m\x00\x07\ttext", 40)
        self.assertNotIn("\x1b", "".join(lines))
        self.assertNotIn("\x00", "".join(lines))
        self.assertNotIn("\x07", "".join(lines))


class TuiInteractionTests(TuiDetailFixture, unittest.TestCase):
    def test_full_preview_is_opt_in_and_selection_returns_to_the_card(self):
        # Exercise the real curses loop with a deterministic screen, not only its
        # pure layout helpers. Nothing is indexed or resumed by this test.
        long_text = "\n".join(f"Finding {n}: explain the skill." for n in range(90))
        self.conn.execute("UPDATE messages SET text=?,nl_text=? WHERE id=?", (long_text, long_text, self.match))
        second_id = self.add("Second session answer", "assistant", 1, sid="session-two", path="/second.jsonl")
        second = {**self.entry, "n": 2, "session_id": "session-two", "match_id": second_id,
                  "title": "Second session"}
        frames = []

        class Screen:
            def __init__(self):
                self.keys = iter(["\x14", curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_NPAGE,
                                  curses.KEY_DOWN, curses.KEY_UP, "\x14", "\x14", "\x1b"])
                self.writes = []
            def getmaxyx(self): return 40, 120
            def erase(self): self.writes = []
            def addnstr(self, y, x, text, n, attr=0):
                self.writes.append((y, x, text[:n]))
            def move(self, *args): pass
            def refresh(self): frames.append(list(self.writes))
            def keypad(self, *args): pass
            def timeout(self, *args): pass
            def get_wch(self): return next(self.keys)

        screen = Screen()
        args = SimpleNamespace(query="explain skill", regex=False, semantic=False)
        with mock.patch.object(curses, "wrapper", side_effect=lambda fn: fn(screen)), \
                mock.patch.object(curses, "curs_set"), mock.patch.object(curses, "use_default_colors"), \
                mock.patch.object(curses, "init_pair"), mock.patch.object(curses, "color_pair", return_value=0), \
                mock.patch.object(recall, "_build_results", return_value=([self.entry, second], False)), \
                mock.patch.object(recall, "_resume") as resume:
            recall.tui(self.conn, args)
        dx = recall._tui_split_widths(120)[1]
        def detail(frame):
            return "".join(text for y, x, text in frame if y >= 2 and x >= dx)
        self.assertIn("WHY THIS MATCHED", detail(frames[0]))
        self.assertNotIn("FULL PREVIEW", detail(frames[0]))
        self.assertIn("FULL PREVIEW", detail(frames[1]))
        self.assertIn("Finding 0:", detail(frames[1]))
        self.assertNotIn("Finding 0:", detail(frames[2]))
        self.assertIn("Finding 0:", detail(frames[3]))
        self.assertIn("Second session answer", detail(frames[5]))
        self.assertNotIn("FULL PREVIEW", detail(frames[5]))
        self.assertIn("WHY THIS MATCHED", detail(frames[6]))
        self.assertNotIn("FULL PREVIEW", detail(frames[6]))
        self.assertIn("Finding 0:", detail(frames[7]))
        self.assertIn("FULL PREVIEW", detail(frames[7]))
        self.assertNotIn("FULL PREVIEW", detail(frames[8]))
        self.assertTrue(all(0 <= y < 40 and 0 <= x < 120 for frame in frames for y, x, _ in frame))
        resume.assert_not_called()
