---
title: "The 44-Minute CI Job (and the One Query Behind It)"
description: "A release-gate benchmark suddenly took 44 minutes. The cause was one search query running a 10-billion-operation join — and the fix took it from ~9 minutes to ~1 second."
pubDate: 2026-08-20
tags: ["engineering", "sqlite", "performance", "ci"]
---

Our release pipeline's quality check suddenly took 44 minutes. The fix turned out to be one search query — and cutting it from ~9 minutes to ~1 second brought the whole job down to 45 seconds.

## What CI was running

Before every npm release, a benchmark runs as a gate: if search quality or speed has regressed, the release is blocked. The benchmark builds a synthetic database of 100,000 fake conversation sessions, then measures three things about our search engine:

1. **Quality** — do the test queries still find the right sessions?
2. **Latency** — how fast are searches at that size?
3. **Resources** — how much disk and memory does the index use?

One detail worth knowing: the benchmark doesn't test a copy of our search code. It calls the exact same functions our real `recall search` command uses — it just points them at synthetic data instead of real transcripts. So when the benchmark is slow, real searches are slow too.

## Where the 44 minutes went

Everything finished in under a minute — except one measurement: fuzzy search latency. At 100k sessions, a single fuzzy search took about 9 minutes. The benchmark times that search five times. 5 × 9 minutes ≈ 44 minutes. That was the whole job.

## The bug: one query, two lists

Fuzzy search answers "which sessions mention these words?" in two steps:

1. Find **eligible sessions** — sessions that mention all of the searched words (possibly across different messages).
2. Find the **best matching messages** inside those sessions.

The old code did both in one giant query, keeping the eligible list as a "CTE" — a temporary named subquery. Here's the catch: **a CTE can't have an index.** An index lets the database answer "is this session eligible?" directly, without reading the whole list. Without one, every check means scanning all 100,000 entries.

So the query ran as: *"for each of the 100,000 eligible sessions, scan all 100,000 messages."* That's 10 billion checks — hence 9 minutes per search.

## The fix: CTE → temp table

We made the fast strategy the only possible one. Instead of keeping the eligible list inside the query as an unindexed CTE, we moved it into a **temp table with a primary key** — which gives the database an index for "is this session eligible?". Now the database scans the matching messages once, and each eligibility check is an instant lookup.

- That query: ~9 minutes → **~1 second**
- Whole 100k benchmark: 44 minutes → **45 seconds**
- Search results: **identical** (all quality tests still pass — we only changed *how* the database combines the lists, not *what* it returns)

The fix is [commit fa8da8f](https://github.com/viggy28/recall/commit/fa8da8ff0767c6f224725d5437b483319b55e0c2) — one function (`search_fuzzy`), thirty lines changed.

## How we debugged it

The frustrating part: **it wouldn't reproduce on my Mac.** Same code, same data, seconds instead of 44 minutes. Three steps cracked it:

1. **Reproduce in CI's world.** We ran the exact same benchmark inside a Docker container mimicking CI's Linux environment — it hung for 10+ minutes. Reproduced. (For reference: the [nightly run that took 44 minutes](https://github.com/viggy28/recall/actions/runs/32097299116) and the [publish run that looked hung](https://github.com/viggy28/recall/actions/runs/32007395179).)
2. **Ask the database what it plans to do.** SQLite's `EXPLAIN QUERY PLAN` shows the execution order it chose. It literally showed the terrible order: scan the eligible list, and re-scan the entire search index for each eligible session.
3. **Prove it's the engine, not our code.** We ran the same query against the same database file on two SQLite versions side by side — the old one (CI's) hung; the new one (my Mac's) finished in 0.8 seconds.

## Same query, different database versions

This is the part that made the bug invisible locally: **every machine ships a different SQLite version.** macOS bundles a recent one; CI's Linux runners ship an older one. Both are SQLite — same SQL, same data — but the part that decides how to run a query (the planner) improved between versions. The old planner estimated the terrible strategy as cheap and picked it. The new one picked the smart strategy on its own.

That's why local testing never caught it: my Mac's database engine quietly did the right thing, hiding a bug that was always there.

## What's the benefit of CI? This is the answer

This is exactly what CI is for. A bug that was invisible on the author's machine, at small scale, got caught by the CI gate at large scale on a different engine — **before** it shipped. Without the benchmark, we'd have released a version where any user with a large index could hit multi-minute searches. The gate is annoying when it's slow, but this time it caught a real problem in our production code.

## The lesson

Performance isn't portable. The same query can be 1,000× slower on a different machine with a different database version — and you'll never see it if you only test where the database is "smart enough." Benchmarks at scale, on the environment that matches production, are what surface these. And the best fix is often not tuning the query, but restructuring it so the database has only one good option.
