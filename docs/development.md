# Development and architecture

## Setup

Create a virtual environment and install the package in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Include optional semantic-search dependencies when needed:

```bash
pip install -e ".[semantic]"
```

## Tests

Run the Python test suite:

```bash
python3 -m unittest -v
```

Type-check the Pi extension:

```bash
npx tsc --project tsconfig.recall.json
```

## Architecture

The `recall.py` module and CLI remain the compatibility facade. The data lifecycle is separated into focused modules:

- `recall_core.ingestion` discovers Claude Code, Pi, and Codex transcripts and normalizes their records into a shared message shape.
- `recall_core.indexing` owns the SQLite schema, FTS5 indexes, connections, and incremental persistence.
- `recall_core.retrieval` owns query filters, fuzzy and regular-expression search, ranking, and match-preview formatting.
- `recall_core.graph` extracts normalized entities, builds weighted co-occurrence graphs, and renders JSON or DOT artifacts.

Semantic embedding and CLI/TUI orchestration currently remain in the facade. Keeping them there avoids changing optional-dependency loading and the public module surface while the core is separated into subsystems.

## Demo assets

Regenerate the README walkthrough with [VHS](https://github.com/charmbracelet/vhs):

```bash
vhs docs/recall-demo.tape
```

The tape runs real Recall commands from the checkout. For its semantic-search segment, install the optional dependencies and build embeddings first:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[semantic]"
recall index --semantic
```
