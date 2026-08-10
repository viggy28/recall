# recall

`recall` is a local work-memory tool for coding-agent sessions. It indexes local JSONL transcripts from Claude Code, Pi, and Codex into a SQLite database so you can search prior work, resume sessions, and manage reusable Markdown context banks.

Core fuzzy/regex search and indexing use only the Python standard library. Semantic search is optional and local-only via `fastembed` and `numpy`.

## What it indexes

- Claude Code transcripts under `~/.claude/projects`
- Pi sessions under `~/.pi/agent/sessions` or `PI_CODING_AGENT_SESSION_DIR`
- Codex sessions under `$CODEX_HOME/sessions` or `~/.codex/sessions`

State is stored under `~/.recall`:

- `~/.recall/recall.db` — SQLite index
- `~/.recall/contexts/` — reusable Markdown context banks

## CLI usage

```bash
python3 recall.py --help
python3 recall.py index
python3 recall.py search "deadlock investigation"
python3 recall.py search --regex "IndexError|sqlite3"
python3 recall.py search --semantic "why did we change the retry logic"
python3 recall.py recent
python3 recall.py tui
```

Search options include source, project, role, date range, result limit, JSON output, and typo-tolerant fuzzy matching.

```bash
python3 recall.py search "migration plan" --source pi --project recall --role user --limit 20 --json
```

## Context banks

`recall` can create, import, export, edit, delete, and generate reusable Markdown context files.

```bash
python3 recall.py context create events-db
python3 recall.py context list
python3 recall.py context show events-db
python3 recall.py context path events-db
python3 recall.py context import ./handoff.md --name events-db
python3 recall.py context export events-db ./events-db.md
python3 recall.py context delete events-db --force
python3 recall.py context generate events-db --session <session-id-prefix>
```

## Pi extension

This package includes a Pi extension at `extensions/recall/index.ts`.

It provides:

- `/recall` — interactive dashboard for search, recent sessions, contexts, and maintenance
- `recall_search` — tool for searching local session history
- `recall_context` — tool for listing, showing, attaching, or saving context banks

## Development

Run the test suite with:

```bash
python3 -m unittest -v
```

Package metadata is in `package.json`; the Python backend is `recall.py`.
