"""Persist normalized transcript messages in Recall's SQLite index."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .ingestion import ClaudeCodeSource, CodexSource, OpenCodeSource, PiSource, _epoch

HOME = Path.home()
STATE_DIR = HOME / ".recall"
DB_PATH = STATE_DIR / "recall.db"
TEXT_NORMALIZATION_VERSION = "1"


def _sanitize_db_text(value):
    """Return SQLite-safe text without changing valid Unicode.

    JSON permits escaped UTF-16 surrogates that UTF-8 and SQLite cannot encode.
    Repair valid pairs, replace lone surrogates, and make embedded NULs visible
    in the indexed copy. Source transcripts remain untouched.
    """
    if not isinstance(value, str) or not any(
            ch == "\x00" or 0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        return value
    out: list[str] = []
    i = 0
    while i < len(value):
        code = ord(value[i])
        if 0xD800 <= code <= 0xDBFF and i + 1 < len(value):
            low = ord(value[i + 1])
            if 0xDC00 <= low <= 0xDFFF:
                out.append(chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00))
                i += 2
                continue
        if value[i] == "\x00" or 0xD800 <= code <= 0xDFFF:
            out.append("\ufffd")
        else:
            out.append(value[i])
        i += 1
    return "".join(out)


def _sanitize_db_row(values):
    return tuple(_sanitize_db_text(value) for value in values)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.create_function("regexp", 2, lambda p, s: 1 if s and re.search(p, s) else 0)
    conn.create_function("editdist", 2, _levenshtein)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, session_id TEXT, source TEXT, project TEXT,
    size INTEGER, mtime REAL, byte_offset INTEGER, lines INTEGER,
    present INTEGER DEFAULT 1, last_indexed REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, path TEXT, session_id TEXT, source TEXT, project TEXT,
    role TEXT, type TEXT, ts TEXT, epoch REAL, line_no INTEGER, text TEXT, nl_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_path ON messages(path);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_msg_epoch ON messages(epoch);
CREATE INDEX IF NOT EXISTS idx_msg_session_source_epoch
    ON messages(session_id, source, epoch DESC, id DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text, content='messages', content_rowid='id', tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS messages_trgm USING fts5(
    text, content='messages', content_rowid='id', tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS messages_vocab USING fts5vocab('messages_fts', 'row');

-- keep the external-content FTS indexes in sync with messages automatically
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
    INSERT INTO messages_trgm(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO messages_trgm(messages_trgm, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO messages_trgm(messages_trgm, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
    INSERT INTO messages_trgm(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, message_id INTEGER, session_id TEXT, ord INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS idx_chunk_msg ON chunks(message_id);
CREATE TABLE IF NOT EXISTS embeddings (chunk_id INTEGER PRIMARY KEY, vec BLOB);
CREATE TABLE IF NOT EXISTS embed_meta (model TEXT, dim INTEGER);
CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _normalization_key(source) -> str:
    return f"text-normalization:{source.name}"


def _normalization_is_current(conn, source) -> bool:
    row = conn.execute(
        "SELECT value FROM index_meta WHERE key=?", (_normalization_key(source),)
    ).fetchone()
    return bool(row and row[0] == TEXT_NORMALIZATION_VERSION)


def _repair_normalization(conn, source) -> int:
    """One-time in-place repair of already-indexed text.

    Rewrites only rows whose stored text differs from its sanitized form,
    without re-reading source files (so it is safe even for archived or
    since-removed transcripts). Rowids are preserved so chunk/embedding
    references stay valid, and the AFTER UPDATE trigger keeps FTS in sync.
    """
    repaired = 0
    for r in conn.execute(
        "SELECT id,path,session_id,project,text,nl_text FROM messages WHERE source=?",
        (source.name,),
    ):
        path, sid, project, text, nl = _sanitize_db_row((
            r["path"], r["session_id"], r["project"], r["text"], r["nl_text"],
        ))
        if (path, sid, project, text, nl) == (
                r["path"], r["session_id"], r["project"], r["text"], r["nl_text"]):
            continue
        conn.execute(
            "UPDATE messages SET path=?,session_id=?,project=?,text=?,nl_text=? WHERE id=?",
            (path, sid, project, text, nl, r["id"]),
        )
        repaired += 1

    for r in conn.execute(
        "SELECT path,session_id,project FROM files WHERE source=?", (source.name,)
    ):
        path, sid, project = _sanitize_db_row((r["path"], r["session_id"], r["project"]))
        if (path, sid, project) == (r["path"], r["session_id"], r["project"]):
            continue
        conn.execute(
            "UPDATE files SET path=?,session_id=?,project=? WHERE path=?",
            (path, sid, project, r["path"]),
        )

    conn.execute(
        "INSERT INTO index_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_normalization_key(source), TEXT_NORMALIZATION_VERSION),
    )
    conn.commit()
    return repaired


# --------------------------------------------------------------------------- #
# Indexing
# --------------------------------------------------------------------------- #
def _reindex_file(conn, source, path, row, full: bool):
    """Index one file incrementally (append) or fully. Returns #lines added."""
    st = path.stat()
    size, mtime = st.st_size, st.st_mtime
    path_key = _sanitize_db_text(str(path))
    sid = _sanitize_db_text(source.session_id(path))

    offset, base_line = 0, 0
    project = row["project"] if row else None
    if row and not full:
        # unchanged → skip
        if size == row["size"] and mtime == row["mtime"]:
            conn.execute("UPDATE files SET present=1 WHERE path=?", (path_key,))
            return 0
        # grew → append from stored offset (offset sits on a line boundary)
        if size > row["size"]:
            offset, base_line = row["byte_offset"], row["lines"]
        else:
            # shrank or same-size rewrite → full replacement
            conn.execute("DELETE FROM messages WHERE path=?", (path_key,))
    else:
        # new file or --full → wipe and re-read whole file (scoped by path,
        # since subagent transcripts share a session_id with their parent session)
        conn.execute("DELETE FROM messages WHERE path=?", (path_key,))

    with open(path, "rb") as fh:
        fh.seek(offset)
        raw = fh.read()

    # Hold back a trailing partial line using raw byte positions. Measuring the
    # repaired Unicode text would corrupt the next offset when invalid UTF-8 was
    # replaced by the three-byte U+FFFD representation.
    if raw and not raw.endswith(b"\n"):
        newline = raw.rfind(b"\n")
        if newline == -1:
            complete, consumed = b"", offset
        else:
            complete, consumed = raw[:newline + 1], offset + newline + 1
    else:
        complete, consumed = raw, offset + len(raw)
    encoding = "utf-8-sig" if offset == 0 else "utf-8"
    text = complete.decode(encoding, errors="replace")
    # `complete` is empty or newline-terminated; split only on JSONL's byte
    # delimiter so valid Unicode line-separator characters remain in strings.
    lines = text.split("\n")[:-1]
    records = list(source.parse_lines(lines))

    # resolve project (cwd) from any record that carries it. On an incremental
    # append `project` is already restored from the stored file row, so a source
    # whose cwd lives only in the line-1 header (Pi) doesn't need it re-read.
    if project is None:
        for rec, _ in records:
            cwd = source.record_cwd(rec)
            if cwd:
                project = cwd
                break

    added = 0
    rows = []
    file_mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    for rec, i in records:
        out = source.extract(rec)
        if not out:
            continue
        txt, nl, role, rtype = out
        ts = source.record_ts(rec)
        ep = _epoch(ts) if ts else mtime
        rows.append(_sanitize_db_row((
            path_key, sid, source.name, project, role, rtype,
            ts or file_mtime_iso, ep, base_line + i, txt, nl,
        )))
        added += 1
    if rows:
        conn.executemany(
            "INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    conn.execute(
        "INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed)"
        " VALUES(?,?,?,?,?,?,?,?,1,?)"
        " ON CONFLICT(path) DO UPDATE SET session_id=excluded.session_id,source=excluded.source,"
        " project=excluded.project,size=excluded.size,mtime=excluded.mtime,"
        " byte_offset=excluded.byte_offset,lines=excluded.lines,present=1,last_indexed=excluded.last_indexed",
        _sanitize_db_row((path_key, sid, source.name, project, size, mtime, consumed,
                          base_line + len(lines), datetime.now(timezone.utc).timestamp())))
    return added


# harnesses indexed together — a single DB holds all sources, results mix + tag.
SOURCES = [ClaudeCodeSource, PiSource, CodexSource, OpenCodeSource]


def _index_opencode(conn, source, full=False, purge_missing=False, quiet=False):
    """Snapshot OpenCode's normalized session/message/part SQLite tables."""
    init_db(conn)
    disk = source.files()
    if not disk:
        missing = conn.execute("SELECT COUNT(*) FROM files WHERE source=?", (source.name,)).fetchone()[0]
        if purge_missing:
            conn.execute("DELETE FROM messages WHERE source=?", (source.name,))
            conn.execute("DELETE FROM files WHERE source=?", (source.name,))
        else:
            conn.execute("UPDATE files SET present=0 WHERE source=?", (source.name,))
        conn.commit()
        if not quiet:
            print(f"  {source.name}: 0 files (+0 messages, {missing} missing{'→purged' if purge_missing else '→archived'})")
        return 0
    db = disk[0]
    db_key = _sanitize_db_text(str(db))
    st = db.stat()
    # OpenCode uses WAL mode, so active writes may only touch the `-wal` file.
    wal = Path(str(db) + "-wal")
    wal_st = wal.stat() if wal.exists() else None
    source_size = st.st_size + (wal_st.st_size if wal_st else 0)
    source_mtime = max(st.st_mtime, wal_st.st_mtime if wal_st else st.st_mtime)
    prior = conn.execute("SELECT size,mtime FROM files WHERE path=? AND source=?", (db_key, source.name)).fetchone()
    if prior and not full and prior["size"] == source_size and prior["mtime"] == source_mtime:
        conn.execute("UPDATE files SET present=1 WHERE source=?", (source.name,))
        conn.commit()
        return 0
    remote = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    remote.row_factory = sqlite3.Row
    sessions = {r["id"]: r for r in remote.execute("SELECT id,directory,title,time_created FROM session")}
    parts: dict[str, list[dict]] = {}
    for row in remote.execute("SELECT message_id,data FROM part ORDER BY message_id,id"):
        try:
            parts.setdefault(row["message_id"], []).append(json.loads(row["data"]))
        except (TypeError, json.JSONDecodeError):
            continue
    messages = list(remote.execute("SELECT id,session_id,time_created,data FROM message ORDER BY time_created,id"))
    remote.close()
    conn.execute("DELETE FROM messages WHERE source=?", (source.name,))
    conn.execute("DELETE FROM files WHERE source=?", (source.name,))
    added = 0
    for line, row in enumerate(messages):
        session = sessions.get(row["session_id"])
        if not session:
            continue
        try:
            info = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            continue
        out = source.extract_message(info, parts.get(row["id"], []))
        if not out:
            continue
        text, nl, role, rtype = out
        epoch = row["time_created"] / 1000
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
        virtual_path = f"{db_key}#{_sanitize_db_text(row['session_id'])}"
        conn.execute("INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     _sanitize_db_row((virtual_path, row["session_id"], source.name,
                                       session["directory"], role, rtype, ts, epoch,
                                       line, text, nl)))
        added += 1
    now = datetime.now(timezone.utc).timestamp()
    conn.execute("INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) VALUES(?,?,?,?,?,?,?,?,1,?)",
                 _sanitize_db_row((db_key, "", source.name, None, source_size,
                                   source_mtime, source_size, len(messages), now)))
    for sid, session in sessions.items():
        virtual_path = f"{db_key}#{_sanitize_db_text(sid)}"
        conn.execute("INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) VALUES(?,?,?,?,?,?,?,?,1,?)",
                     _sanitize_db_row((virtual_path, sid, source.name, session["directory"],
                                       source_size, source_mtime, source_size,
                                       len(messages), now)))
    conn.commit()
    if not quiet:
        print(f"  {source.name}: 1 files (+{added} messages, 0 missing)")
    return added


def index(conn, source=None, full=False, purge_missing=False, quiet=False):
    """Index one source's files (incremental by mtime). Bookkeeping is scoped
    `WHERE source=?`, so sources never touch each other's rows. Derived tables
    (embeddings) are handled once by `index_all`, not here."""
    source = source or ClaudeCodeSource()
    init_db(conn)
    if not _normalization_is_current(conn, source):
        _repair_normalization(conn, source)
    if isinstance(source, OpenCodeSource):
        return _index_opencode(conn, source, full, purge_missing, quiet)
    disk = source.files()
    seen = set()
    total_added = 0
    existing = {r["path"]: r for r in conn.execute(
        "SELECT * FROM files WHERE source=?", (source.name,))}
    for path in disk:
        path_key = _sanitize_db_text(str(path))
        seen.add(path_key)
        try:
            total_added += _reindex_file(conn, source, path, existing.get(path_key), full)
        except (OSError, sqlite3.Error) as e:
            if not quiet:
                print(f"  ! skip {path.name}: {e}", file=sys.stderr)
    conn.commit()

    # handle transcripts that disappeared from disk (this source only)
    missing = [p for p in existing if p not in seen]
    if missing:
        if purge_missing:
            for p in missing:
                conn.execute("DELETE FROM messages WHERE path=?", (p,))
                conn.execute("DELETE FROM files WHERE path=?", (p,))
        else:
            conn.executemany("UPDATE files SET present=0 WHERE path=?", [(p,) for p in missing])
        conn.commit()

    if not quiet:
        print(f"  {source.name}: {len(disk)} files (+{total_added} messages, "
              f"{len(missing)} missing{'→purged' if purge_missing else '→archived'})")
    return total_added
