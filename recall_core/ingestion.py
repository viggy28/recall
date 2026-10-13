"""Load and normalize coding-agent transcripts.

This module deliberately knows nothing about SQLite or search. Sources turn
harness-specific JSONL records into a common message representation.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
PROJECTS_DIR = HOME / ".claude" / "projects"
PI_SESSIONS_DIR = HOME / ".pi" / "agent" / "sessions"
OPENCODE_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "opencode"
CHATGPT_IMPORT_DIR = HOME / ".recall" / "imports" / "chatgpt"
MAX_MSG_CHARS = 100_000
MAX_TOOL_INPUT = 2_000
TITLE_FIELDS = {
    "ai-title": "aiTitle", "custom-title": "customTitle",
    "agent-name": "agentName", "last-prompt": "lastPrompt",
    "summary": "summary",
}

def _pi_root() -> Path:
    """Pi's session root, honoring the PI_CODING_AGENT_SESSION_DIR override."""
    env = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    return Path(env).expanduser() if env else PI_SESSIONS_DIR


def _codex_root() -> Path:
    """Codex's session root ($CODEX_HOME/sessions, default ~/.codex/sessions)."""
    home = os.environ.get("CODEX_HOME")
    return (Path(home).expanduser() if home else HOME / ".codex") / "sessions"


def _opencode_db() -> Path:
    """OpenCode's SQLite database, honoring its documented path overrides."""
    value = os.environ.get("OPENCODE_DB")
    if value and value != ":memory:":
        path = Path(value).expanduser()
        return path if path.is_absolute() else OPENCODE_DATA_DIR / path
    return OPENCODE_DATA_DIR / "opencode.db"


def _chatgpt_path() -> Path:
    """ChatGPT export file/directory, defaulting to Recall's local import dir."""
    value = os.environ.get("CHATGPT_EXPORT_PATH")
    return Path(value).expanduser() if value else CHATGPT_IMPORT_DIR

MAX_MSG_CHARS = 100_000      # cap a single message's indexed text
MAX_TOOL_INPUT = 2_000       # cap a tool_use input blob
EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384-dim, small ONNX model
# bge-small reads at most 512 tokens (~1.8-2k chars) and truncates the rest, so
# there's no recall gained by going bigger — size chunks just under that ceiling
# to minimize chunk count (and embedding time) without losing text to truncation.
CHUNK_TARGET = 1_500         # ~paragraph window size for semantic chunks
CHUNK_MAX = 2_000

# Record types that carry searchable text. Everything else (mode,
# permission-mode, file-history-snapshot, attachment, queue-operation,
# agent-setting, system diagnostics) is skipped.
TITLE_FIELDS = {
    "ai-title": "aiTitle",
    "custom-title": "customTitle",
    "agent-name": "agentName",
    "last-prompt": "lastPrompt",
    "summary": "summary",        # legacy transcripts
}


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #
def _flatten_content(content) -> str:
    """Flatten a message.content (str | list-of-blocks) into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            parts.append(block.get("thinking", ""))
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = json.dumps(block.get("input", {}), ensure_ascii=False)
            if len(inp) > MAX_TOOL_INPUT:
                inp = inp[:MAX_TOOL_INPUT] + "…"
            parts.append(f"[tool: {name}] {inp}")
        elif btype == "tool_result":
            parts.append(_flatten_content(block.get("content")))
        elif btype == "image":
            parts.append("[image]")
        # ignore other block kinds
    return "\n".join(p for p in parts if p)


def _nl_content(content) -> str:
    """Natural-language-only text for semantic embedding: typed strings and
    `text` blocks only — no tool calls, tool results, thinking, or images.
    Source-agnostic (Pi reuses it — its text blocks share the `text`/`text` shape)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text")


def _cap(text: str, marker: bool = False) -> str:
    """Truncate an indexed field to MAX_MSG_CHARS (guards against giant pastes)."""
    if len(text) <= MAX_MSG_CHARS:
        return text
    return text[:MAX_MSG_CHARS] + ("\n…[truncated]" if marker else "")


def _pi_flatten(content) -> str:
    """Flatten a Pi message.content block list into plain text for fuzzy/regex.

    Pi's block vocabulary differs from Claude's: `toolCall` (name+arguments),
    `thinking` (+opaque signature), and `image` blocks that embed base64 `data`
    up to ~2 MB — that `data` is dropped to `[image]` so it never reaches the
    row store or the trigram index.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            parts.append(block.get("thinking", ""))       # text only, never nl_text
        elif btype == "toolCall":
            name = block.get("name", "")
            args = json.dumps(block.get("arguments", {}), ensure_ascii=False)
            if len(args) > MAX_TOOL_INPUT:
                args = args[:MAX_TOOL_INPUT] + "…"
            parts.append(f"[tool: {name}] {args}")
        elif btype == "image":
            parts.append("[image]")                        # drop the base64 data
        # ignore other block kinds
    return "\n".join(p for p in parts if p)


def _epoch(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Source abstraction — one reader per harness. A single DB stores all sources
# side by side (messages/files carry a `source` column); results mix and are
# tagged. Adding a harness (Codex, Claude Desktop) is a new subclass.
# --------------------------------------------------------------------------- #
class Source:
    """Base reader. Subclasses set `name`, implement `files()`/`session_id()`/
    `extract()`, and override the record accessors if their field names differ."""
    name = "source"
    whole_document = False

    @staticmethod
    def parse_lines(lines: list[str]):
        """Yield (record, line_offset_in_batch) for valid JSON lines (JSONL)."""
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), i
            except json.JSONDecodeError:
                continue

    def record_cwd(self, record: dict):
        """Project path carried by this record, if any."""
        return record.get("cwd")

    def record_ts(self, record: dict):
        """ISO-8601 timestamp for this record (parsed by _epoch)."""
        return record.get("timestamp")

    def record_session_id(self, record: dict, path: Path) -> str:
        """Session id for a record; whole-document exports may override this."""
        return self.session_id(path)

    def read_bytes(self, path: Path) -> bytes:
        """Read the source payload represented by path."""
        return path.read_bytes()

    def extract(self, record: dict) -> tuple[str, str, str, str] | None:
        """Return (text, nl_text, role, type) for an indexable record, or None."""
        raise NotImplementedError


class ClaudeCodeSource(Source):
    name = "claude-code"

    def __init__(self, root: Path = PROJECTS_DIR):
        self.root = root

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.jsonl"))

    @staticmethod
    def session_id(path: Path) -> str:
        # subagent transcripts live under <project>/<session-id>/subagents/…
        # (flat: subagents/agent-*.jsonl, or nested: subagents/workflows/<wf>/agent-*.jsonl)
        # — attribute them to the parent session so --resume targets a real session.
        parts = path.parts
        if "subagents" in parts:
            i = parts.index("subagents")
            if i > 0:
                return parts[i - 1]        # the <session-id> dir above subagents/
        return path.stem

    def extract(self, record: dict) -> tuple[str, str, str, str] | None:
        """Return (text, nl_text, role, type) for an indexable record, or None.

        `text` is the full flattened content (for fuzzy/regex); `nl_text` is the
        natural-language subset (for semantic embedding) and may be empty (e.g. a
        tool-result-only message), so that message is then skipped by semantic.
        """
        rtype = record.get("type")
        if rtype in TITLE_FIELDS:
            text = record.get(TITLE_FIELDS[rtype], "")
            return (text, text, "meta", rtype) if text else None
        if rtype in ("user", "assistant"):
            msg = record.get("message") or {}
            content = msg.get("content")
            text = _flatten_content(content)
            if not text:
                return None
            nl = _nl_content(content)
            role = msg.get("role") or rtype
            return (_cap(text, marker=True), _cap(nl), role, rtype)
        return None


class PiSource(Source):
    """Pi coding-agent transcripts under ~/.pi/agent/sessions/<cwd-slug>/.

    Layout: `<ISO-ts>_<uuid>.jsonl`, one session per file. Line 1 is a `session`
    header carrying the `cwd`; message records use Pi's own block vocabulary and
    carry `toolResult` as a top-level role (not nested in a user turn).
    """
    name = "pi"

    def __init__(self, root: Path | None = None):
        self.root = root or _pi_root()

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.jsonl"))

    @staticmethod
    def session_id(path: Path) -> str:
        # stem is "<ISO-ts>_<uuid>"; the ISO prefix uses '-', so the first '_'
        # cleanly splits off the uuid (which is the session id).
        _, _, uuid = path.stem.partition("_")
        return uuid or path.stem

    def record_cwd(self, record: dict):
        # cwd is recorded once, on the line-1 `session` header.
        return record.get("cwd") if record.get("type") == "session" else None

    def extract(self, record: dict) -> tuple[str, str, str, str] | None:
        if record.get("type") != "message":
            return None                    # session / model_change / thinking_level_change
        msg = record.get("message") or {}
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant"):
            text = _pi_flatten(content)
            if not text:
                return None
            return (_cap(text, marker=True), _cap(_nl_content(content)), role, role)
        if role == "toolResult":
            text = _pi_flatten(content)
            if not text:
                return None
            return (_cap(text), "", "tool", "toolResult")
        return None


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _codex_text(content) -> str:
    """Flatten a Codex content value (str, or list of {type,text} blocks)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("text"))


class CodexSource(Source):
    """OpenAI Codex rollout transcripts under ~/.codex/sessions/YYYY/MM/DD/.

    Layout: `rollout-<ISO>-<uuid>.jsonl`. Every line is `{timestamp,type,payload}`.
    Line 1 is `session_meta` (carries session_id + cwd); the canonical transcript
    lives in `response_item` records (OpenAI Responses items: messages with
    input_text/output_text blocks, function calls, tool outputs). The parallel
    `event_msg` UI stream is skipped to avoid double-indexing.
    """
    name = "codex"

    def __init__(self, root: Path | None = None):
        self.root = root or _codex_root()

    def files(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.rglob("*.jsonl"))

    @staticmethod
    def session_id(path: Path) -> str:
        # filename is rollout-<ISO>-<uuid>; both use '-', so match the UUID shape.
        m = _UUID_RE.search(path.stem)
        return m.group(0) if m else path.stem

    def record_cwd(self, record: dict):
        # cwd is recorded once, on the line-1 `session_meta` record's payload.
        if record.get("type") == "session_meta":
            return (record.get("payload") or {}).get("cwd")
        return None

    def extract(self, record: dict) -> tuple[str, str, str, str] | None:
        if record.get("type") != "response_item":
            return None            # session_meta / event_msg / turn_context / world_state / compacted
        p = record.get("payload") or {}
        pt = p.get("type")
        if pt == "message":
            role = p.get("role")
            if role not in ("user", "assistant"):
                return None        # skip `developer` (system/permission boilerplate)
            text = _codex_text(p.get("content"))
            if not text:
                return None
            return (_cap(text, marker=True), _cap(text), role, role)
        if pt in ("function_call", "custom_tool_call"):
            name = p.get("name", "")
            arg = p.get("arguments") if pt == "function_call" else p.get("input")
            arg = arg if isinstance(arg, str) else json.dumps(arg or {}, ensure_ascii=False)
            if len(arg) > MAX_TOOL_INPUT:
                arg = arg[:MAX_TOOL_INPUT] + "…"
            return (f"[tool: {name}] {arg}", "", "tool", pt)
        if pt in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
            text = _codex_text(p.get("output"))
            return (_cap(text), "", "tool", pt) if text else None
        if pt == "web_search_call":
            q = (p.get("action") or {}).get("query", "")
            return (f"[tool: web_search] {q}", "", "tool", pt) if q else None
        return None                # reasoning (encrypted), tool_search_call, etc.


def _chatgpt_iso(value) -> str | None:
    """Normalize ChatGPT's Unix timestamps (or existing ISO strings) to UTC ISO."""
    if value is None or isinstance(value, bool):
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return value if isinstance(value, str) and _epoch(value) is not None else None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _chatgpt_text(content) -> str:
    """Extract textual parts from a ChatGPT export message content object."""
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    values: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, str):
                values.append(part)
            elif isinstance(part, dict):
                value = part.get("text")
                if isinstance(value, str):
                    values.append(value)
    direct = content.get("text")
    if not values and isinstance(direct, str):
        values.append(direct)
    return "\n".join(value for value in values if value)


class ChatGPTSource(Source):
    """ChatGPT data exports (`conversations.json` or the original export ZIP).

    Each export is one JSON array containing many conversation trees. Only the
    branch ending at `current_node` is indexed, so regenerated/abandoned replies
    do not appear beside the branch visible in ChatGPT.
    """
    name = "chatgpt"
    whole_document = True

    def __init__(self, root: Path | None = None):
        self.root = root or _chatgpt_path()

    def files(self) -> list[Path]:
        if self.root.is_file():
            return [self.root] if self.root.suffix.lower() in (".json", ".zip") else []
        if not self.root.is_dir():
            return []
        paths = list(self.root.rglob("conversations*.json"))
        paths.extend(self.root.rglob("*.zip"))
        return sorted(set(paths))

    @staticmethod
    def session_id(path: Path) -> str:
        return path.stem

    def read_bytes(self, path: Path) -> bytes:
        if path.suffix.lower() != ".zip":
            return super().read_bytes(path)
        try:
            with zipfile.ZipFile(path) as archive:
                candidates = [name for name in archive.namelist()
                              if re.fullmatch(r"conversations(?:-\d+)?\.json",
                                              name.rstrip("/").split("/")[-1])]
                conversations = []
                for member in sorted(candidates):
                    try:
                        value = json.loads(archive.read(member).decode("utf-8-sig"))
                    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(value, list):
                        conversations.extend(value)
                return json.dumps(conversations, ensure_ascii=False).encode("utf-8")
        except (OSError, zipfile.BadZipFile):
            return b""

    @staticmethod
    def _active_nodes(conversation: dict) -> list[dict]:
        mapping = conversation.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            return []
        current = conversation.get("current_node")
        if not isinstance(current, str) or current not in mapping:
            parents = {node.get("parent") for node in mapping.values()
                       if isinstance(node, dict) and isinstance(node.get("parent"), str)}
            leaves = [(key, node) for key, node in mapping.items()
                      if isinstance(node, dict) and key not in parents]
            if not leaves:
                return []
            def recency(item):
                message = item[1].get("message") or {}
                if not isinstance(message, dict):
                    return 0.0
                value = message.get("update_time") or message.get("create_time") or 0
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
            current = max(leaves, key=recency)[0]

        branch: list[dict] = []
        seen: set[str] = set()
        while isinstance(current, str) and current in mapping and current not in seen:
            seen.add(current)
            node = mapping[current]
            if not isinstance(node, dict):
                break
            branch.append(node)
            current = node.get("parent")
        branch.reverse()
        return branch

    @staticmethod
    def parse_lines(lines: list[str]):
        try:
            payload = json.loads("\n".join(lines))
        except (TypeError, json.JSONDecodeError):
            return
        conversations = payload if isinstance(payload, list) else payload.get("conversations", []) \
            if isinstance(payload, dict) else []
        offset = 0
        for conversation in conversations:
            if not isinstance(conversation, dict):
                continue
            sid = conversation.get("conversation_id") or conversation.get("id")
            if not isinstance(sid, str) or not sid:
                continue
            base_ts = _chatgpt_iso(conversation.get("create_time") or conversation.get("update_time"))
            title = conversation.get("title")
            if isinstance(title, str) and title.strip():
                yield {"kind": "title", "session_id": sid, "text": title.strip(),
                       "timestamp": base_ts}, offset
                offset += 1
            for node in ChatGPTSource._active_nodes(conversation):
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                metadata = message.get("metadata") or {}
                if isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation"):
                    continue
                author = message.get("author") or {}
                role = author.get("role") if isinstance(author, dict) else None
                if role not in ("user", "assistant"):
                    continue
                text = _chatgpt_text(message.get("content"))
                if not text:
                    continue
                yield {"kind": "message", "session_id": sid, "role": role,
                       "text": text,
                       "timestamp": _chatgpt_iso(message.get("create_time")) or base_ts}, offset
                offset += 1

    def record_session_id(self, record: dict, path: Path) -> str:
        return record.get("session_id") or self.session_id(path)

    def extract(self, record: dict) -> tuple[str, str, str, str] | None:
        text = record.get("text")
        if not isinstance(text, str) or not text:
            return None
        if record.get("kind") == "title":
            return (_cap(text), _cap(text), "meta", "ai-title")
        role = record.get("role")
        if role not in ("user", "assistant"):
            return None
        return (_cap(text, marker=True), _cap(text), role, role)


def _opencode_flatten(parts) -> tuple[str, str]:
    """Flatten OpenCode V1 message parts, using Pi-like tool formatting."""
    full: list[str] = []
    natural: list[str] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            value = part.get("text", "")
            full.append(value)
            natural.append(value)
        elif ptype == "reasoning":
            full.append(part.get("text", ""))
        elif ptype in ("tool", "tool-invocation"):
            tool = part.get("toolInvocation") or part
            state = tool.get("state") or {}
            name = part.get("tool") or tool.get("toolName", "")
            args = state.get("input") if isinstance(state, dict) else None
            if args is None:
                args = tool.get("args", {})
            value = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
            full.append(f"[tool: {name}] {value[:MAX_TOOL_INPUT]}" + ("…" if len(value) > MAX_TOOL_INPUT else ""))
            if isinstance(state, dict) and state.get("status") == "completed":
                output = state.get("output")
                if output:
                    full.append(str(output))
            elif tool.get("state") == "result" and tool.get("result"):
                full.append(str(tool["result"]))
        elif ptype == "file":
            full.append(f"[file: {part.get('filename') or part.get('mime') or part.get('mediaType', '')}]")
    return "\n".join(filter(None, full)), "\n".join(filter(None, natural))


class OpenCodeSource(Source):
    """OpenCode conversations stored in its data-directory SQLite database."""
    name = "opencode"

    def __init__(self, db: Path | None = None):
        self.db = db or _opencode_db()

    def files(self) -> list[Path]:
        return [self.db] if self.db.is_file() else []

    @staticmethod
    def session_id(path: Path) -> str:
        return path.stem

    def extract_message(self, info: dict, parts: list[dict]):
        role = info.get("role")
        if role not in ("user", "assistant"):
            return None
        text, nl = _opencode_flatten(parts)
        return (_cap(text, marker=True), _cap(nl), role, role) if text else None
