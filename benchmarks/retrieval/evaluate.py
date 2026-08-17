"""Shared PR, nightly, and release retrieval evaluator."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import recall
from recall_core.indexing import connect

from .corpus import (
    DEFAULT_SEED, corpus_fingerprint, load_spec, materialize_scale_embeddings,
    percentile, populate_corpus,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_BASELINE = ROOT / "baseline.json"
REPORT_SCHEMA = 1
MODEL_MANIFEST = ROOT / "semantic-model.json"


def verify_semantic_model() -> dict[str, Any]:
    manifest = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    if manifest["model_name"] != recall.EMBED_MODEL:
        raise RuntimeError("semantic model manifest does not match Recall configuration")
    cache = os.environ.get("RECALL_EMBED_CACHE")
    if not cache:
        raise RuntimeError("RECALL_EMBED_CACHE is required for reproducible semantic evaluation")
    repository = "models--" + manifest["fastembed_repository"].replace("/", "--")
    snapshot = Path(cache).expanduser() / repository / "snapshots" / manifest["revision"]
    for name, expected in manifest["files"].items():
        path = snapshot / name
        if not path.exists():
            raise RuntimeError(f"pinned semantic model file is missing: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"pinned semantic model digest mismatch: {name}")
    return {"revision": manifest["revision"], "manifest": str(MODEL_MANIFEST)}


def make_args(query: str, *, typo: bool = False, limit: int = 20) -> SimpleNamespace:
    return SimpleNamespace(query=query, typo=typo, project=None, source=None,
                           role=None, since=None, until=None, limit=limit)


def ranked_session_ids(rows) -> list[str]:
    return list(dict.fromkeys(row["session_id"] for row in rows))


def search(conn, mode: str, query: str, *, typo: bool = False,
           limit: int = 20) -> list[str]:
    args = make_args(query, typo=typo, limit=limit)
    if mode == "fuzzy":
        rows = recall.search_fuzzy(conn, args)
    elif mode == "regex":
        rows = recall.search_regex(conn, args)
    elif mode == "semantic":
        rows = recall.search_semantic(conn, args)
    else:
        raise ValueError(f"unknown retrieval mode: {mode}")
    return ranked_session_ids(rows)


def query_metric(ranked: list[str], expected: list[str], max_rank: int) -> dict[str, Any]:
    ranks = [ranked.index(sid) + 1 for sid in expected if sid in ranked]
    best_rank = min(ranks) if ranks else None
    recall = len(ranks) / len(expected)
    reciprocal_rank = 1.0 / best_rank if best_rank else 0.0
    return {
        "expected": expected,
        "ranked": ranked[:max(10, max_rank)],
        "best_rank": best_rank,
        "max_rank": max_rank,
        "recall": recall,
        "reciprocal_rank": reciprocal_rank,
        "passed": recall == 1.0 and best_rank is not None and best_rank <= max_rank,
    }


def evaluate_quality(conn, semantic: bool) -> dict[str, Any]:
    spec = load_spec()
    results: dict[str, Any] = {}
    for case in spec["queries"]:
        if case["mode"] == "semantic" and not semantic:
            continue
        ranked = search(conn, case["mode"], case["query"],
                        typo=bool(case.get("typo")), limit=20)
        metric = query_metric(ranked, case["expected"], int(case["max_rank"]))
        metric.update({"mode": case["mode"], "query": case["query"]})
        results[case["id"]] = metric
    passed = sum(1 for result in results.values() if result["passed"])
    return {
        "queries": results,
        "passed": passed,
        "total": len(results),
        "pass_rate": passed / len(results) if results else 0.0,
        "mrr": statistics.fmean(r["reciprocal_rank"] for r in results.values())
               if results else 0.0,
    }


def _timed(fn, samples: int) -> dict[str, Any]:
    values = []
    for _ in range(samples):
        started = time.perf_counter()
        fn()
        values.append((time.perf_counter() - started) * 1_000)
    return {
        "samples_ms": values,
        "p50_ms": statistics.median(values),
        "p95_ms": percentile(values, 0.95),
    }


def evaluate_latency(db_path: Path, semantic: bool, samples: int) -> dict[str, Any]:
    cases = [
        ("fuzzy", "synthetic benchmark archive"),
        ("regex", "SYNTHETIC_SENTINEL"),
    ]
    if semantic:
        cases.append(("semantic", "routine archival benchmark workload"))
    metrics: dict[str, Any] = {}
    for mode, query in cases:
        recall._EMB_CACHE = None
        cold_conn = connect(db_path)
        started = time.perf_counter()
        search(cold_conn, mode, query, limit=20)
        cold_ms = (time.perf_counter() - started) * 1_000
        cold_conn.close()

        recall._EMB_CACHE = None
        warm_conn = connect(db_path)
        search(warm_conn, mode, query, limit=20)  # warm SQLite/model/matrix caches
        timed = _timed(lambda: search(warm_conn, mode, query, limit=20), samples)
        warm_conn.close()
        metrics[mode] = {"query": query, "fresh_connection_ms": cold_ms, **timed}
    return metrics


def _peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1024 * 1024) if sys.platform == "darwin" else value / 1024


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def environment() -> dict[str, Any]:
    packages = {}
    for name in ("fastembed", "numpy", "onnxruntime", "tokenizers"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
        "commit": _git("rev-parse", "HEAD") or os.environ.get("GITHUB_SHA"),
        "ref": _git("describe", "--tags", "--always") or os.environ.get("GITHUB_REF"),
        "packages": packages,
        "semantic_model": recall.EMBED_MODEL,
    }


def compare_baseline(report: dict[str, Any], baseline: dict[str, Any],
                     enforce_performance: bool) -> list[str]:
    failures = []
    if baseline.get("corpus_version") != report["corpus"]["version"]:
        failures.append("baseline corpus version does not match candidate")
    quality = report["quality"]
    if quality["pass_rate"] < baseline["quality"]["minimum_pass_rate"]:
        failures.append("retrieval pass rate is below the approved minimum")
    if quality["mrr"] < baseline["quality"]["minimum_mrr"]:
        failures.append("retrieval MRR is below the approved minimum")
    if enforce_performance:
        limits = baseline.get("performance", {}).get(str(report["corpus"]["sessions"]))
        if not limits:
            failures.append("approved baseline has no limits for this corpus size")
        else:
            index_seconds = (report["index"]["initial_seconds"] +
                             report["index"]["incremental_seconds"])
            if index_seconds > limits["max_index_seconds"]:
                failures.append("index construction exceeded the catastrophic ceiling")
            worst_p95 = max(m["p95_ms"] for m in report["latency"].values())
            if worst_p95 > limits["max_warm_p95_ms"]:
                failures.append("query p95 exceeded the catastrophic ceiling")
            if report["storage"]["database_mb"] > limits["max_db_mb"]:
                failures.append("database size exceeded the catastrophic ceiling")
            rss_limit = limits.get("max_peak_rss_mb")
            if rss_limit and report["memory"]["peak_rss_mb"] > rss_limit:
                failures.append("peak RSS exceeded the catastrophic ceiling")
    return failures


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Retrieval evaluation",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Sessions: {report['corpus']['sessions']:,}",
        f"- Corpus: `{report['corpus']['version']}` / `{report['corpus']['fingerprint'][:16]}`",
        f"- Quality: {report['quality']['passed']}/{report['quality']['total']} passed; "
        f"MRR {report['quality']['mrr']:.3f}",
        f"- Database: {report['storage']['database_mb']:.2f} MB",
        f"- Peak RSS: {report['memory']['peak_rss_mb']:.2f} MB",
        "",
        "## Queries",
        "",
        "| ID | Mode | Rank | Limit | Result |",
        "|---|---|---:|---:|---|",
    ]
    for qid, result in report["quality"]["queries"].items():
        lines.append(f"| {qid} | {result['mode']} | {result['best_rank'] or '-'} | "
                     f"{result['max_rank']} | {'pass' if result['passed'] else 'FAIL'} |")
    lines.extend(["", "## Warm latency", "",
                  "| Mode | p50 ms | p95 ms | Fresh connection ms |",
                  "|---|---:|---:|---:|"])
    for mode, metric in report["latency"].items():
        lines.append(f"| {mode} | {metric['p50_ms']:.2f} | {metric['p95_ms']:.2f} | "
                     f"{metric['fresh_connection_ms']:.2f} |")
    if report.get("failures"):
        lines.extend(["", "## Blocking findings", ""])
        lines.extend(f"- {failure}" for failure in report["failures"])
    lines.extend(["", "## Environment", "", "```json",
                  json.dumps(report["environment"], indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    spec = load_spec()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / f"retrieval-{args.sessions}.db"
    db_path.unlink(missing_ok=True)

    conn = connect(db_path)
    corpus = populate_corpus(conn, args.sessions, args.seed)
    semantic = bool(args.semantic)
    semantic_scale_vectors = 0
    semantic_provenance = None
    if semantic:
        recall._EMB_CACHE = None
        recall.build_embeddings(conn, quiet=True, rechunk=True)
        semantic_provenance = verify_semantic_model()
        semantic_scale_vectors = materialize_scale_embeddings(conn)
        conn.execute("DELETE FROM embed_meta")
        conn.execute("INSERT INTO embed_meta(model,dim) VALUES(?,?)", (recall.EMBED_MODEL, 384))
        conn.commit()
        recall._EMB_CACHE = None
    quality = evaluate_quality(conn, semantic)
    conn.close()

    samples = 5 if args.profile == "pr" else 3
    latency = evaluate_latency(db_path, semantic, samples)
    db_bytes = sum(path.stat().st_size for path in db_path.parent.glob(db_path.name + "*"))
    report = {
        "schema_version": REPORT_SCHEMA,
        "profile": args.profile,
        "command": " ".join(sys.argv),
        "corpus": {
            "version": spec["corpus_version"], "seed": args.seed,
            "sessions": corpus["sessions"], "messages": corpus["messages"],
            "fingerprint": corpus_fingerprint(args.sessions, args.seed, spec),
            "vocabulary_terms": corpus["vocabulary_terms"],
            "semantic_scale_vectors": semantic_scale_vectors,
            "semantic_provenance": semantic_provenance,
            "semantic_scale_note": (
                "Golden texts use the pinned real model; filler sessions use deterministic zero "
                "vectors to measure matrix/storage scale without claiming 100K model inferences."
                if semantic else None),
        },
        "index": {
            "initial_seconds": corpus["initial_index_seconds"],
            "incremental_seconds": corpus["incremental_index_seconds"],
            "incremental_sessions": corpus["incremental_sessions"],
        },
        "quality": quality,
        "latency": latency,
        "storage": {"database_bytes": db_bytes, "database_mb": db_bytes / 1_000_000},
        "memory": {"peak_rss_mb": _peak_rss_mb()},
        "environment": environment(),
        "baseline": None,
        "failures": [],
    }
    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    report["baseline"] = {
        "path": str(baseline_path), "name": baseline.get("name"),
        "source_tag": baseline.get("source_tag"),
        "source_commit": baseline.get("source_commit"),
    }
    if args.candidate_tag and args.candidate_tag == baseline.get("source_tag"):
        report["failures"].append("candidate tag must not bless or compare against itself")
    report["failures"].extend(compare_baseline(
        report, baseline, enforce_performance=args.profile == "release"))
    if any(not q["passed"] for q in quality["queries"].values()):
        report["failures"].append("one or more required golden queries failed")

    (output_dir / "results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "summary.md").write_text(markdown_summary(report), encoding="utf-8")
    return report, 1 if report["failures"] else 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("pr", "nightly", "release"), required=True)
    parser.add_argument("--sessions", type=int, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--candidate-tag")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    output_dir = Path(args.output_dir)
    try:
        report, code = run(args)
        print(markdown_summary(report))
        return code
    except Exception as exc:
        output_dir.mkdir(parents=True, exist_ok=True)
        failure = {
            "schema_version": REPORT_SCHEMA,
            "profile": args.profile,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "environment": environment(),
        }
        (output_dir / "results.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output_dir / "summary.md").write_text(
            f"# Retrieval evaluation failed\n\n`{failure['error']}`\n", encoding="utf-8")
        print(failure["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
