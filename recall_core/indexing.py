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

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, message_id INTEGER, session_id TEXT, ord INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS idx_chunk_msg ON chunks(message_id);
CREATE TABLE IF NOT EXISTS embeddings (chunk_id INTEGER PRIMARY KEY, vec BLOB);
CREATE TABLE IF NOT EXISTS embed_meta (model TEXT, dim INTEGER);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# Indexing
# --------------------------------------------------------------------------- #
def _reindex_file(conn, source, path, row, full: bool):
    """Index one file incrementally (append) or fully. Returns #lines added."""
    st = path.stat()
    size, mtime = st.st_size, st.st_mtime
    sid = source.session_id(path)

    offset, base_line = 0, 0
    project = row["project"] if row else None
    if row and not full and size >= row["size"] and size > 0:
        # unchanged → skip
        if size == row["size"] and mtime == row["mtime"]:
            conn.execute("UPDATE files SET present=1 WHERE path=?", (str(path),))
            return 0
        # grew → append from stored offset (offset sits on a line boundary)
        offset, base_line = row["byte_offset"], row["lines"]
    else:
        # new file, shrank, or --full → wipe and re-read whole file (scoped by path,
        # since subagent transcripts share a session_id with their parent session)
        conn.execute("DELETE FROM messages WHERE path=?", (str(path),))

    with open(path, "rb") as fh:
        fh.seek(offset)
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    # hold back a trailing partial line (file may be mid-append)
    consumed = len(raw)
    if text and not text.endswith("\n"):
        nl = text.rfind("\n")
        if nl == -1:
            text, consumed = "", offset  # no complete line yet
        else:
            consumed = offset + len(text[: nl + 1].encode("utf-8"))
            text = text[: nl + 1]
    else:
        consumed = offset + len(raw)

    lines = text.splitlines()
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
        rows.append((str(path), sid, source.name, project, role, rtype,
                     ts or file_mtime_iso, ep, base_line + i, txt, nl))
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
        (str(path), sid, source.name, project, size, mtime, consumed,
         base_line + len(lines), datetime.now(timezone.utc).timestamp()))
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
    st = db.stat()
    # OpenCode uses WAL mode, so active writes may only touch the `-wal` file.
    wal = Path(str(db) + "-wal")
    wal_st = wal.stat() if wal.exists() else None
    source_size = st.st_size + (wal_st.st_size if wal_st else 0)
    source_mtime = max(st.st_mtime, wal_st.st_mtime if wal_st else st.st_mtime)
    prior = conn.execute("SELECT size,mtime FROM files WHERE path=? AND source=?", (str(db), source.name)).fetchone()
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
        virtual_path = f"{db}#{row['session_id']}"
        conn.execute("INSERT INTO messages(path,session_id,source,project,role,type,ts,epoch,line_no,text,nl_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (virtual_path, row["session_id"], source.name, session["directory"], role, rtype, ts, epoch, line, text, nl))
        added += 1
    now = datetime.now(timezone.utc).timestamp()
    conn.execute("INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) VALUES(?,?,?,?,?,?,?,?,1,?)",
                 (str(db), "", source.name, None, source_size, source_mtime, source_size, len(messages), now))
    for sid, session in sessions.items():
        virtual_path = f"{db}#{sid}"
        conn.execute("INSERT INTO files(path,session_id,source,project,size,mtime,byte_offset,lines,present,last_indexed) VALUES(?,?,?,?,?,?,?,?,1,?)",
                     (virtual_path, sid, source.name, session["directory"], source_size, source_mtime, source_size, len(messages), now))
    conn.commit()
    if not quiet:
        print(f"  {source.name}: 1 files (+{added} messages, 0 missing)")
    return added


def index(conn, source=None, full=False, purge_missing=False, quiet=False):
    """Index one source's files (incremental by mtime). Bookkeeping is scoped
    `WHERE source=?`, so sources never touch each other's rows. Derived tables
    (embeddings) are handled once by `index_all`, not here."""
    source = source or ClaudeCodeSource()
    if isinstance(source, OpenCodeSource):
        return _index_opencode(conn, source, full, purge_missing, quiet)
    init_db(conn)
    disk = source.files()
    seen = set()
    total_added = 0
    existing = {r["path"]: r for r in conn.execute(
        "SELECT * FROM files WHERE source=?", (source.name,))}
    for path in disk:
        seen.add(str(path))
        try:
            total_added += _reindex_file(conn, source, path, existing.get(str(path)), full)
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
