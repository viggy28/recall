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

The configured embedding model is downloaded and run locally. Embeddings remain in the local SQLite database. Semantic search uses the embeddings already stored there and never rebuilds them during search or TUI startup. `recall tui --semantic` exits with indexing guidance when no semantic index exists; when Recall reports that an existing semantic index is stale, refresh it explicitly with `recall index --semantic`.

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

Context banks are plain Markdown files created from selected evidence and an explicit focus. Recall never treats a context name or short description as project evidence.

```bash
# Interactive: search the existing local index, select one or more sessions,
# then choose Durable project, Current task, Decision history, or Custom focus.
recall context create streambed-internals

# Explicit indexed sessions (repeat --session).
recall context create streambed-internals \
  --session 019fcf9a \
  --session 019fd039 \
  --focus-preset durable

# Explicit repository evidence, inspected only after approval.
recall context create streambed-marketing \
  --source ~/source/github/acme/streambed \
  --focus "Positioning, target users, value proposition, and proof points"

# The only from-scratch creation path.
recall context create scratch --blank

recall context list
recall context show streambed-internals
recall context update streambed-internals "The migration is complete."
recall context undo streambed-internals
recall context import ./handoff.md --name imported-handoff
recall context export streambed-internals ./streambed-internals.md
recall context delete streambed-internals --force
```

With no source flag in a terminal, `context create` searches already-indexed session titles and messages locally. It shows five ranked matches at a time and supports show-more, refined search, and comma-separated multi-selection. Creation never triggers indexing. In non-interactive use, select `--session`, `--source`, or `--blank` explicitly.

Focus is a synthesis lens, not evidence. Opinionated focuses keep the generated body to at most 15 Markdown lines and 150 words, and the complete initial context to at most 20 lines including compact provenance, while selecting the most important themes or decisions instead of producing exhaustive implementation documentation. **Durable project** (recommended) preserves the difference between shipped behavior and historical proposals; **Current task** creates a dated handoff; **Decision history** preserves adopted, proposed, rejected, and superseded status. A custom focus adds a topic-specific lens with conservative evidence handling. The focus policy is applied during every chunk summary and the final synthesis.

Model-backed creation separates two decisions: first approve sending the displayed evidence to the displayed provider/model, then review the generated draft and separately Apply, Revise focus, use the Full editor, or Cancel. `context update` keeps its focused diff review. For model-free update scripts, repeat `--replace OLD NEW`.

In Pi, the `recall_context` tool uses the same Python creation engine and adds Pi-native source selection and review UI. Ask naturally:

```text
Create a Streambed internals context focused on implementation details.
Attach the streambed-internals context.
Update the streambed-internals context with the rollout notes.
```

For repository-aware creation, provide the path explicitly. Recall never guesses a path or silently substitutes Pi's current directory:

```text
Create a Streambed marketing context from the source, focused on positioning and value proposition.
~/source/github/acme/streambed
```

Before touching a repository directory, Recall shows its absolute lexical path, generation provider/model, fixed collection limits, and disclosure. A symlinked root requires confirmation of its canonical target. Revise reuses the approved bounded snapshot rather than reading the repository again.

## Privacy

Recall reads transcript files and writes its SQLite index and Markdown contexts locally. Core indexing, fuzzy search, regex search, discovery, blank creation, and ordinary context-bank management do not send transcript content over the network. Session-backed creation sends only the selected transcript evidence after the displayed transmission approval.

Repository-backed context creation is another explicit exception: only after you approve source inspection, Recall performs a bounded, read-only collection and sends the bounded file listing, omission metadata, selected source excerpts, and focus to the displayed Pi generation provider/model. It prefers Git-tracked files and otherwise uses bounded traversal; skips symlinks, secret-like files and high-confidence secret content, dependencies, generated output, and binaries; truncates oversized text excerpts to the disclosed per-file and total limits; and displays selected paths and omission/truncation counts before save approval. Filtering reduces accidental disclosure but cannot guarantee that source contains no sensitive information, so review the displayed path, provider/model, and selected-path summary carefully. Denying the initial approval performs no source stat, Git command, read, generation, or write.

Optional semantic search downloads its configured embedding model through `fastembed`, runs it locally, and stores embeddings in the local database.
