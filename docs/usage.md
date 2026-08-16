# Recall usage

Recall can run as a standalone Python CLI or as an extension inside Pi. Both modes use the same local SQLite index and Markdown context banks under `~/.recall`.

## What Recall indexes

- Claude Code transcripts under `~/.claude/projects`
- Pi sessions under `~/.pi/agent/sessions` or `PI_CODING_AGENT_SESSION_DIR`
- Codex sessions under `$CODEX_HOME/sessions` or `~/.codex/sessions`
- OpenCode sessions in `$OPENCODE_DB` or `$XDG_DATA_HOME/opencode/opencode.db` (normally `~/.local/share/opencode/opencode.db`)

Local state is stored at:

- `~/.recall/recall.db` — SQLite index
- `~/.recall/contexts/` — reusable Markdown context banks
- `~/.recall/context-history/` — previous context-bank revisions

## Standalone CLI

### No-install usage

Core indexing, fuzzy search, and regex search have no required third-party Python packages:

```bash
python3 recall.py index
python3 recall.py search "deadlock investigation"
```

### Install the `recall` command

Use a virtual environment to isolate Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
recall --help
```

Update an editable install with `git pull` in the checkout. If you installed from a package archive, reinstall the newer package in the same virtual environment.

## Search and session commands

```bash
recall index
recall search "deadlock investigation"
recall search --regex "IndexError|sqlite3"
recall recent
recall tui
```

Search can filter by source, project, role, date range, and result limit. It also supports JSON output and typo-tolerant fuzzy matching:

```bash
recall search "migration plan" --source pi --project recall --role user --limit 20 --json
recall search "retry backoff" --source opencode
```

If you have not installed the package, replace `recall` with `python3 recall.py` in these commands.

## Knowledge graphs

`recall graph` extracts named entities with a conservative local heuristic and connects entities that occur in the same message. The output includes weighted edges and references to the source messages. No model or network access is required.

Generate JSON for D3, Cytoscape, or other graph tools:

```bash
recall graph --max-nodes 75 --min-edge-weight 2 > graph.json
```

Generate a scoped Graphviz DOT file:

```bash
recall graph --source pi --project recall --since 2026-01-01 \
  --entity-type organization --format dot --output graph.dot
dot -Tsvg graph.dot > graph.svg
```

Scope a graph with `--since`, `--until`, `--source`, `--project`, or `--entity-type`. Use `--max-nodes` and `--min-edge-weight` to keep dense graphs readable. The built-in extractor recognizes capitalized names, acronyms, domains, `@people`, and `#topics`.

## Optional local semantic search

Semantic indexing and search require `fastembed` and `numpy`. Install them with the `semantic` extra:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[semantic]"
recall index --semantic
recall search --semantic "why did we change the retry logic"
```

To install only the optional dependencies without installing the package:

```bash
pip install -r requirements-semantic.txt
python3 recall.py index --semantic
```

The configured embedding model is downloaded and run locally. Embeddings remain in the local SQLite database.

## Pi extension

Install the published Pi package:

```bash
pi install npm:recall-pi
```

Update it later with:

```bash
pi update npm:recall-pi
```

To install from a local checkout while developing:

```bash
pi install /path/to/recall
```

The extension provides:

- `/recall` — interactive dashboard for search, recent sessions, contexts, and maintenance
- `recall_search` — search local session history
- `recall_context` — list, show, create, update, and attach context banks

The extension runs the bundled `recall.py` backend. It selects Python in this order:

1. `RECALL_PYTHON`, when set
2. `.venv/bin/python` inside the installed package
3. `python3`

For semantic search, install the optional dependencies into a Python environment the extension will use:

```bash
cd /path/to/recall-package
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[semantic]"
```

Alternatively, point the extension to another environment:

```bash
export RECALL_PYTHON=/absolute/path/to/venv/bin/python
```

## Context banks

Context banks are plain Markdown files that can be reviewed, revised, reused, imported, and exported.

```bash
recall context create events-db "Track the durable state and open questions for the Events DB"
recall context create scratch --blank
recall context list
recall context show events-db
recall context path events-db
recall context update events-db "The migration is complete; remove the resolved question."
recall context undo events-db
recall context import ./handoff.md --name events-db
recall context export events-db ./events-db.md
recall context delete events-db --force
recall context generate events-db --session <session-id-prefix>
```

`context create` and `context update` accept natural-language instructions, show a focused preview, and offer Apply, Revise, Full editor, or Cancel. Use `create --blank` for an empty template. For model-free update scripts, repeat `--replace OLD NEW`.

In Pi, the `recall_context` tool provides the same review flow. You can ask naturally:

```text
Create an events-db context that tracks durable state and open questions.
Attach the events-db context before we continue the migration.
Update the events-db context with the production rollout notes.
```

## Privacy

Recall reads transcript files and writes its SQLite index and Markdown contexts locally. Core indexing, fuzzy search, regex search, and context-bank management do not send transcript content over the network.

Optional semantic search downloads its configured embedding model through `fastembed`, runs it locally, and stores embeddings in the local database.
