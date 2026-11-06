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

## Testing the Pi extension from a checkout

Load the complete checkout so the TypeScript extension and Python backend stay together:

```bash
pi install /absolute/path/to/recall
```

If `npm:recall-pi` is already configured, remove that package entry with `pi remove npm:recall-pi` to avoid loading both copies. Run `/reload` in existing Pi sessions after changing the package source.

Do not copy only `extensions/recall/index.ts` into an installed npm package. Recall runs the `recall.py` bundled beside its extension, not the `recall` executable on PATH. Mixing a newer extension with an older backend can fail at `context discover` before any approval screen opens, even when the standalone CLI works.

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

- `recall_core.ingestion` discovers Claude Code, Pi, Codex, OpenCode, and ChatGPT transcripts and normalizes their records into a shared message shape.
- `recall_core.indexing` owns the SQLite schema, FTS5 indexes, connections, and incremental persistence.
- `recall_core.retrieval` owns query filters, fuzzy and regular-expression search, ranking, and match-preview formatting.
- `recall_core.context_creation` is the canonical source-first creation engine: local session discovery/ranking, focus-aware structured generation, and bounded repository evidence collection.
- `recall_core.context_policy` owns the versioned Markdown contract, section budgets, writing/update policy, and validation. New generation, CLI updates/edits/imports, and Pi saves use this policy. The Pi extension calls `context validate --input-file` with a private temporary JSON proposal; validation does not index, invoke a model, fetch links, or write contexts. New creation requires v2 (title/summary/actions first, compact evidence IDs, metadata footer). Updates preserve v1 and unmarked legacy layouts; no format is converted in place.

The [editorial module](../recall_core/context_editorial.py) builds the shared Summary-review prompt and validates the model’s JSON response. It permits Summary-only edits, preserves supporting material and provenance, and fails closed on unresolved conflicts or invalid responses. `context editorial --input-file` only builds prompts or applies/validates a supplied response: it performs no model call, indexing, link fetching, or context save. CLI and Pi orchestration supply the same model and evidence before human approval.

The Pi extension uses the backend's JSON discovery/draft/validation/editorial protocols and adapts source selection, approvals, and review rendering. Its `tool_call` guard blocks direct built-in write/edit calls to managed context/history paths, including normalized paths and symlink aliases; reviewed internal filesystem operations do not use those tools. This is an accidental-write guard, not an operating-system sandbox. Tests exercise the shared validator through real Python subprocesses, including Pi creation/editor/update saves and legacy preservation. The [update-evidence tests](../extensions/recall/update-evidence.test.ts) exercise the production proposal pipeline from a vague instruction through session-evidence selection, patching, and editorial review without saving. Selection uses `buildSessionProjection()` so compaction and context edits are honored; raw history is never a fallback. Empty edit arrays have a different error from malformed patch responses, and neither triggers a blind retry.

Semantic embedding and CLI/TUI orchestration currently remain in the facade. Keeping them there avoids changing optional-dependency loading and the public module surface while the core is separated into subsystems.

### Editorial regression evaluation

The [editorial tests](../tests/test_context_editorial.py) cover omitted impact, uncertainty, Summary-only mutation, failed reviews, and protocol behavior. Two opt-in live evaluations use fictional evidence and never save a context:

```bash
RECALL_EDITORIAL_MODEL=default python3 -m unittest -v tests.test_context_editorial.LiveEditorialRegressionTests
# Or select the same explicit provider/model for both cases:
RECALL_EDITORIAL_MODEL=provider/model python3 -m unittest -v tests.test_context_editorial.LiveEditorialRegressionTests
```

A live pass is evidence about those cases, not a general guarantee of good writing or factual correctness.

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
