"""Generate deterministic normalized Recall corpora for retrieval evaluation."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from recall_core.indexing import init_db

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "spec.json"
DEFAULT_SEED = 350036
BATCH_SIZE = 2_000


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec.get("schema_version") != 1:
        raise ValueError("unsupported retrieval spec schema")
    session_ids = [s["id"] for s in spec.get("sessions", [])]
    query_ids = [q["id"] for q in spec.get("queries", [])]
    if len(session_ids) != len(set(session_ids)) or len(query_ids) != len(set(query_ids)):
        raise ValueError("duplicate session or query id in retrieval spec")
    known = set(session_ids)
    for query in spec["queries"]:
        if not query.get("expected") or not set(query["expected"]).issubset(known):
            raise ValueError(f"query {query['id']} references an unknown expected session")
        if int(query.get("max_rank", 0)) < 1:
            raise ValueError(f"query {query['id']} has an invalid max_rank")
    return spec


def corpus_fingerprint(session_count: int, seed: int, spec: dict[str, Any]) -> str:
    payload = json.dumps({
        "corpus_version": spec["corpus_version"],
        "seed": seed,
        "sessions": session_count,
        "spec": spec,
    }, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _chunks(items: Iterable[tuple], size: int = BATCH_SIZE):
    batch: list[tuple] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _timestamp(index: int) -> tuple[str, float]:
    epoch = 1_735_689_600.0 + index
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(), epoch


def _golden_rows(spec: dict[str, Any]):
    message_id = 0
    for sidx, session in enumerate(spec["sessions"]):
        sid = session["id"]
        path = f"/synthetic/golden/{sid}.jsonl"
        for line, message in enumerate(session["messages"], 1):
            message_id += 1
            ts, epoch = _timestamp(message_id)
            yield (
                path, sid, "pi", "/synthetic/golden", message["role"], message["type"],
                ts, epoch, line, message["text"], message.get("nl_text", message["text"]),
            )


def _filler_rows(start: int, stop: int):
    base_text = (
        "synthetic benchmark archive workload routine filler "
        "deterministic retrieval measurement"
    )
    for index in range(start, stop):
        text = base_text + (" SYNTHETIC_SENTINEL" if index % 100 == 0 else "")
        sid = f"bench-{index:06d}"
        path = f"/synthetic/scale/{sid}.jsonl"
        ts, epoch = _timestamp(10_000 + index)
        # Filler natural-language text is empty. Real semantic quality embeds the
        # hand-written corpus; scale vectors are materialized separately and labelled.
        yield (path, sid, "pi", "/synthetic/scale", "assistant", "assistant",
               ts, epoch, 1, text, "")


def _file_rows(message_rows: Iterable[tuple]):
    seen: set[tuple[str, str]] = set()
    for row in message_rows:
        path, sid = row[0], row[1]
        key = (path, sid)
        if key in seen:
            continue
        seen.add(key)
        yield (path, sid, "pi", row[3], 1, 1.0, 1, 1, 1, 1.0)


def _insert_rows(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)", list(_file_rows(rows)),
    )


def populate_corpus(conn: sqlite3.Connection, session_count: int,
                    seed: int = DEFAULT_SEED, incremental_sessions: int = 100) -> dict[str, Any]:
    """Populate exactly ``session_count`` sessions in two deterministic phases.

    The first phase contains the hand-written golden corpus and most scale rows;
    the second phase appends up to ``incremental_sessions`` rows so callers can
    measure a realistic incremental FTS update separately.
    """
    del seed  # Reserved in the corpus contract; generated content is formula-derived.
    spec = load_spec()
    golden_count = len(spec["sessions"])
    if session_count < golden_count:
        raise ValueError(f"session_count must be at least {golden_count}")
    init_db(conn)
    delta = min(incremental_sessions, max(0, session_count - golden_count))
    initial_fillers = session_count - golden_count - delta

    initial_started = time.perf_counter()
    golden = list(_golden_rows(spec))
    _insert_rows(conn, golden)
    for batch in _chunks(_filler_rows(0, initial_fillers)):
        _insert_rows(conn, batch)
    conn.commit()
    initial_seconds = time.perf_counter() - initial_started

    incremental_started = time.perf_counter()
    for batch in _chunks(_filler_rows(initial_fillers, initial_fillers + delta)):
        _insert_rows(conn, batch)
    conn.commit()
    incremental_seconds = time.perf_counter() - incremental_started

    actual = conn.execute("SELECT COUNT(DISTINCT session_id) FROM messages").fetchone()[0]
    if actual != session_count:
        raise RuntimeError(f"generated {actual} sessions, expected {session_count}")
    return {
        "sessions": actual,
        "messages": conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
        "golden_sessions": golden_count,
        "incremental_sessions": delta,
        "vocabulary_terms": conn.execute("SELECT COUNT(*) FROM messages_vocab").fetchone()[0],
        "initial_index_seconds": initial_seconds,
        "incremental_index_seconds": incremental_seconds,
    }


def materialize_scale_embeddings(conn: sqlite3.Connection, dimension: int = 384) -> int:
    """Add deterministic zero vectors for scale-only sessions.

    Golden sessions retain embeddings produced by the pinned real model. Zero
    vectors add realistic 384-float storage/matrix pressure without pretending
    CI performed 100K unique model inferences.
    """
    rows = conn.execute(
        "SELECT m.id,m.session_id FROM messages m "
        "LEFT JOIN chunks c ON c.message_id=m.id "
        "WHERE m.session_id LIKE 'bench-%' AND c.id IS NULL ORDER BY m.id"
    ).fetchall()
    for batch in _chunks(((r[0], r[1], 0, "synthetic vector scale row") for r in rows)):
        conn.executemany(
            "INSERT INTO chunks(message_id,session_id,ord,text) VALUES(?,?,?,?)", batch)
    conn.commit()
    chunk_ids = [r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE session_id LIKE 'bench-%' ORDER BY id")]
    zero = bytes(dimension * 4)
    for batch in _chunks(((cid, zero) for cid in chunk_ids)):
        conn.executemany("INSERT OR REPLACE INTO embeddings(chunk_id,vec) VALUES(?,?)", batch)
    conn.commit()
    return len(chunk_ids)


def percentile(values: list[float], proportion: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of no samples")
    ordered = sorted(values)
    index = max(0, math.ceil(proportion * len(ordered)) - 1)
    return ordered[index]
