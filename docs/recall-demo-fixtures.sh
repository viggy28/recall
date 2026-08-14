# Deterministic shell fixtures used by docs/recall-demo.tape.
# Source this file in a throwaway shell; it defines demo-only recall/pi commands.

export PS1='viggy@mac recall % '

recall() {
  case "$*" in
    "index --stats")
      cat <<'OUT'
db:            /Users/viggy28/.recall/recall.db
size:          187.0 MB
files:         203 (0 archived/missing)
sessions:      127
messages:      19761
vocab terms:   69375
embeddings:    6241 (BAAI/bge-small-en-v1.5, 384d)
OUT
      ;;
    "search migration safe not safe --limit 3")
      cat <<'OUT'
 #  session     matches  when              project
 1  019ffeb0    43 hits  2026-08-14 05:15  ~/Documents/Codex/is-my-migration-safe  codex
    〉 Migration SAFE / NOT SAFE — paste a migration, get one word back.

 2  019fea4a    22 hits  2026-08-13 06:47  ~/source/github/viggy28  pi
    〉 SAFE / NOT SAFE is the strongest idea: searchable pain, weekend-shippable.

 3  019ff9e2    18 hits  2026-08-13 06:54  ~/Documents/Codex/sites-plugin  codex
    〉 Use libpg_query in-browser; LLM only for optional ORM-to-SQL transpile.

resume a session by # or id: recall go <#|id>   ·  3 sessions · fuzzy
OUT
      ;;
    "search --semantic local memory tool for coding agent sessions --limit 3")
      cat <<'OUT'
 #  session   relevance  when              project
 1  019fe8e9   0.71 sim  2026-08-11 00:20  ~/source/github/viggy28/recall  pi
    〉 recall indexes Claude Code, Pi, and Codex transcripts into local SQLite.

 2  019fd5cb   0.69 sim  2026-08-06 06:38  ~/source/github/buildrappo/redactor  codex
    〉 Add project memory so future agent sessions start with durable context.

 3  019fa6ff   0.69 sim  2026-07-30 05:24  ~/source/github/viggy28/openthaw  pi
    〉 A Python CLI for querying local agent history and reusable context banks.

resume a session by # or id: recall go <#|id>   ·  3 sessions · semantic
OUT
      ;;
    "context list")
      cat <<'OUT'
ideas                        2415 bytes  2026-08-12 23:38
migration-safe-notsafe      13703 bytes  2026-08-12 23:44
recall                       2267 bytes  2026-08-13 22:49
OUT
      ;;
    "context show recall")
      cat <<'OUT'
# recall

## What this project is
- Local work memory for coding-agent sessions.
- Indexes Claude Code, Pi, and Codex JSONL transcripts into SQLite.
- Supports fuzzy, regex, and optional local semantic search.
- Provides reusable Markdown context banks under ~/.recall/contexts.
OUT
      ;;
    *)
      command recall "$@"
      ;;
  esac
}

pi() {
  case "$*" in
    "install npm:recall-pi")
      cat <<'OUT'
✓ Installed recall-pi
  extension: /recall
  tools: recall_search, recall_context
OUT
      ;;
    *)
      command pi "$@"
      ;;
  esac
}
