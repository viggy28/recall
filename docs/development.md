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

## Retrieval evaluation

Retrieval quality is a versioned CI contract under `benchmarks/retrieval/`.
The pull-request profile uses a deterministic 1,000-session corpus:

```bash
python3 -m pip install -r benchmarks/retrieval/requirements-ci.txt
python3 -m benchmarks.retrieval.evaluate \
  --profile pr --sessions 1000 --semantic --output-dir /tmp/recall-retrieval
```

Scheduled evaluation reuses the same specification and generator at larger scales:

```bash
python3 -m benchmarks.retrieval.evaluate --profile nightly --sessions 10000 \
  --semantic --output-dir /tmp/recall-retrieval-10k
python3 -m benchmarks.retrieval.evaluate --profile nightly --sessions 100000 \
  --semantic --output-dir /tmp/recall-retrieval-100k
```

Each run writes `results.json` and `summary.md`. Golden texts use the configured
real local embedding model. Scale-only filler sessions use deterministic zero
vectors so CI measures SQLite storage and the production matrix/ranking path
without claiming to benchmark 100,000 unique model inferences.

PR change detection skips retrieval only when every changed path is a known-safe
documentation or static asset. Unknown paths run fail-safe. The GitHub branch
rule should require only `Retrieval CI / Required retrieval gate`, not the
conditional worker job.

Type-check the Pi extension:

```bash
npx tsc --project tsconfig.recall.json
```

## Architecture

The `recall.py` module and CLI remain the compatibility facade. The data lifecycle is separated into focused modules:

- `recall_core.ingestion` discovers Claude Code, Pi, and Codex transcripts and normalizes their records into a shared message shape.
- `recall_core.indexing` owns the SQLite schema, FTS5 indexes, connections, and incremental persistence.
- `recall_core.retrieval` owns query filters, fuzzy and regular-expression search, ranking, and match-preview formatting.

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
