"""Query and rank messages stored in Recall's index."""
from __future__ import annotations

import re
import sqlite3
import sys
import textwrap
import unicodedata
from pathlib import Path

from .indexing import _levenshtein
from .ingestion import _epoch

HOME = Path.home()
STATE_DIR = HOME / ".recall"

_TERM_RE = re.compile(r"\w+", re.UNICODE)


def _fts_terms(query: str) -> list[str]:
    return [t for t in _TERM_RE.findall(query.lower()) if t]


# friendly --source values → the stored `source` column value
_SOURCE_ALIAS = {"claude": "claude-code", "claude-code": "claude-code",
                 "pi": "pi", "codex": "codex"}


def _filters(args):
    """Build extra WHERE clauses + params from common filter flags."""
    where, params = [], []
    if getattr(args, "project", None):
        where.append("m.project LIKE ?")
        params.append(f"%{args.project}%")
    if getattr(args, "source", None):
        where.append("m.source = ?")
        params.append(_SOURCE_ALIAS.get(args.source, args.source))
    if getattr(args, "role", None):
        where.append("m.role = ?")
        params.append(args.role)
    for flag, op in (("since", ">="), ("until", "<=")):
        val = getattr(args, flag, None)
        if val:
            ep = _epoch(val + "T00:00:00Z")
            if ep is not None:
                where.append(f"m.epoch {op} ?")
                params.append(ep)
    return where, params


TTY = sys.stdout.isatty()
HL = ("\033[1;33m", "\033[0m") if TTY else ("»", "«")

# results from the last `recall search` are persisted here so `recall go <N>` can resume
LAST_PATH = STATE_DIR / "recall.last.json"


def _paint(s: str, code: str) -> str:
    """Wrap `s` in an SGR color code — only when writing to a terminal."""
    return f"\033[{code}m{s}\033[0m" if TTY else s


# color roles (basic SGR for portability): bold idx, dim id/date, cyan hits,
# blue path, yellow warnings, faint footer. Matches stay bold-yellow via HL.
C_IDX, C_ID, C_HITS, C_DATE, C_PATH, C_WARN, C_DIM = "1", "2", "36", "2", "34", "33", "2"
C_TITLE = "1"   # bold — the session title is the recognition anchor


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _clip_visible(s: str, width: int) -> str:
    """Truncate to `width` visible columns, ignoring ANSI codes when counting
    and never cutting a code in half. Appends '…' (and a reset) if clipped."""
    if width <= 1:
        return s
    out, vis, i = [], 0, 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        if vis >= width:
            out.append("…" + ("\033[0m" if TTY else ""))
            return "".join(out)
        out.append(s[i])
        vis += 1
        i += 1
    return "".join(out)


def _abbrev_home(path: str | None) -> str:
    if not path:
        return "?"
    home = str(HOME)
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _oneline(s: str) -> str:
    """Collapse all whitespace so a snippet is a single clean line."""
    return re.sub(r"\s+", " ", s).strip()


def _bounded_lines(text: str, width: int, max_lines: int) -> list[str]:
    """Word-wrap plain text into a bounded number of recognition-oriented lines."""
    text = _oneline(text)
    if not text or width < 1 or max_lines < 1:
        return []
    return textwrap.wrap(text, width=width, max_lines=max_lines, placeholder="…",
                         break_long_words=True, break_on_hyphens=False)


def _mark_preview_terms(text: str, query: str, mode: str) -> str:
    """Add the TUI's highlight markers to matches in one wrapped preview line."""
    if mode == "semantic":
        return text
    if mode == "regex":
        try:
            rx = re.compile(query, re.I)
        except re.error:
            return text
    else:
        terms = sorted(set(_fts_terms(query)), key=len, reverse=True)
        if not terms:
            return text
        rx = re.compile("|".join(re.escape(t) for t in terms), re.I)
    return rx.sub(lambda m: HL[0] + m.group(0) + HL[1], text)


def _preview_lines(text: str, query: str, mode: str, width: int,
                   max_lines: int = 4) -> list[str]:
    """Wrap a matched passage, replacing stored markers with fresh highlights."""
    plain = text.replace(HL[0], "").replace(HL[1], "")
    return [_mark_preview_terms(line, query, mode)
            for line in _bounded_lines(plain, width, max_lines)]


def _snippet_from(text: str, terms: list[str], width: int = 160) -> str:
    """Build a one-line, highlighted snippet from `text` around the first
    matching term (used to show natural-language context, not tool noise)."""
    low = text.lower()
    found = [i for i in (low.find(t) for t in terms) if i != -1]
    pos = min(found) if found else 0
    start = max(0, pos - 40)
    frag = _oneline(text[start:start + width])
    for t in terms:
        frag = re.sub(re.escape(t), lambda m: HL[0] + m.group(0) + HL[1], frag, flags=re.I)
    return ("…" if start else "") + frag + ("…" if start + width < len(text) else "")


def _typo_expand(conn, terms, max_terms=40):
    """Expand each term to vocab terms within small edit distance (--typo)."""
    vocab = [r[0] for r in conn.execute("SELECT term FROM messages_vocab")]
    groups = []
    for t in terms:
        thr = 1 if len(t) <= 4 else 2
        cands = {t}
        for v in vocab:
            if abs(len(v) - len(t)) <= thr and _levenshtein(t, v) <= thr:
                cands.add(v)
                if len(cands) >= max_terms:
                    break
        groups.append(cands)
    return groups


def _fts_group(words) -> str:
    """One safely quoted FTS prefix group (alternatives represent a typo expansion)."""
    quoted = [f'"{word.replace(chr(34), chr(34) * 2)}"*' for word in sorted(words)]
    return "(" + " OR ".join(quoted) + ")"


def search_fuzzy(conn, args):
    terms = _fts_terms(args.query)
    if not terms:
        return []
    groups = _typo_expand(conn, terms) if getattr(args, "typo", False) \
        else [{term} for term in terms]
    group_matches = [_fts_group(group) for group in groups]
    all_match = " AND ".join(group_matches)
    any_match = " OR ".join(group_matches)

    where, filter_params = _filters(args)
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    # Search is presented and resumed at session granularity, so term coverage
    # must use that same unit. Build one candidate-session set per term and
    # intersect them; the messages used for previews may then match any term.
    # Sessions with a message containing every term are still ranked first.
    term_ctes, cte_params = [], []
    for i, group_match in enumerate(group_matches):
        term_ctes.append(f"""
            term_{i} AS MATERIALIZED (
                SELECT DISTINCT m.session_id, m.source
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
                WHERE messages_fts MATCH ?{where_sql}
            )""")
        cte_params.extend([group_match, *filter_params])
    eligible = " INTERSECT ".join(
        f"SELECT session_id, source FROM term_{i}" for i in range(len(term_ctes))
    )

    # Rank inside each session before applying a global limit. Keep both the
    # strongest hit and the best conversational hit so previews avoid tool noise.
    sql = f"""
        WITH {','.join(term_ctes)},
        eligible(session_id, source) AS MATERIALIZED ({eligible}),
        co_located(rowid) AS MATERIALIZED (
            SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?
        ),
        matches AS MATERIALIZED (
            SELECT m.session_id, m.source, m.path, m.project, m.ts, m.epoch,
                   m.type, m.nl_text, COALESCE(f.present,1) AS present,
                   snippet(messages_fts, 0, '{HL[0]}', '{HL[1]}', '…', 14) AS snip,
                   bm25(messages_fts) AS raw_score,
                   CASE WHEN c.rowid IS NULL THEN 0 ELSE 1 END AS same_message
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN eligible e ON e.session_id = m.session_id AND e.source = m.source
            LEFT JOIN co_located c ON c.rowid = m.id
            LEFT JOIN files f ON f.path = m.path
            WHERE messages_fts MATCH ?{where_sql}
        ), ranked AS (
            SELECT *,
                   MAX(same_message) OVER (PARTITION BY session_id) AS has_same_message,
                   COUNT(*) OVER (PARTITION BY session_id) AS hit_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY session_id ORDER BY same_message DESC, raw_score
                   ) AS score_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY session_id
                       ORDER BY same_message DESC,
                                CASE WHEN nl_text IS NOT NULL AND nl_text != ''
                                     THEN 0 ELSE 1 END,
                                raw_score
                   ) AS preview_rank
            FROM matches
        )
        SELECT *, raw_score - (has_same_message * 1000000.0) AS score
        FROM ranked
        WHERE score_rank = 1 OR preview_rank = 1
        ORDER BY has_same_message DESC, raw_score
        LIMIT ?
    """
    sql_params = [*cte_params, all_match, any_match, *filter_params,
                  max(20, args.limit * 2)]
    return conn.execute(sql, sql_params).fetchall()


def search_regex(conn, args):
    pattern = args.query
    try:
        re.compile(pattern)
    except re.error as e:
        print(f"bad regex: {e}", file=sys.stderr)
        return []
    where, params = _filters(args)

    # optimization: if the pattern has a mandatory literal run (>=4, no top-level
    # alternation), prefilter candidates via the trigram index before applying re.
    prefilter = ""
    if "|" not in pattern:
        lits = re.findall(r"[A-Za-z0-9_]{4,}", pattern)
        if lits:
            lit = max(lits, key=len)
            prefilter = ("AND m.id IN (SELECT rowid FROM messages_trgm "
                         "WHERE messages_trgm MATCH ?) ")
            params = [lit, *params]
    where_sql = (" AND " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT m.id, m.session_id, m.source, m.path, m.project, m.ts, m.epoch, m.type, m.text,
               COALESCE(f.present,1) AS present
        FROM messages m
        LEFT JOIN files f ON f.session_id = m.session_id AND f.source = m.source
        WHERE m.text REGEXP ? {prefilter}{where_sql}
        ORDER BY m.epoch DESC
    """
    rows = conn.execute(sql, [pattern, *params]).fetchall()
    out = []
    rx = re.compile(pattern)
    for r in rows:
        d = dict(r)
        m = rx.search(r["text"])
        if m:
            s, e = max(0, m.start() - 40), min(len(r["text"]), m.end() + 40)
            frag = r["text"][s:e].replace("\n", " ")
            d["snip"] = (("…" if s else "") + frag[:m.start()-s] + HL[0]
                         + frag[m.start()-s:m.end()-s] + HL[1] + frag[m.end()-s:]
                         + ("…" if e < len(r["text"]) else ""))
        else:
            d["snip"] = r["text"][:100].replace("\n", " ")
        d["score"] = -r["epoch"] if r["epoch"] else 0
        out.append(d)
    return out

