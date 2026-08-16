"""Deterministic, dependency-free knowledge graph generation."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
import re


_ENTITY_RE = re.compile(
    r"(?<![\w@#])(?:[#@][\w.-]+|(?:[A-Z][\w.+-]*)(?:\s+[A-Z][\w.+-]*){0,3}|"
    r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|ai|dev))(?!\w)"
)
_STOP = {
    "a", "an", "and", "assistant", "but", "can", "could", "do", "for", "from",
    "here", "how", "i", "if", "in", "it", "let", "please", "the", "then", "this",
    "to", "use", "user", "we", "what", "when", "where", "why", "you",
}


def _entity_type(value: str) -> str:
    if value.startswith("#"):
        return "topic"
    if value.startswith("@"):
        return "person"
    if "." in value or (value.isupper() and len(value) > 1):
        return "organization"
    return "entity"


def _entity_id(value: str) -> str:
    """Normalize spelling variants such as ``Open AI`` and ``openai.com``."""
    value = value.lstrip("#@").lower()
    value = re.sub(r"\.(?:com|org|net|io|ai|dev)$", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def extract_entities(text: str) -> list[dict[str, str]]:
    """Extract conservative named-entity candidates without an ML dependency."""
    found = {}
    for match in _ENTITY_RE.finditer(text or ""):
        label = match.group(0).strip(".,:;!?()[]{}\"'")
        entity_id = _entity_id(label)
        if len(entity_id) < 2 or entity_id in _STOP:
            continue
        found.setdefault(entity_id, {"id": entity_id, "label": label.lstrip("#@"),
                                     "type": _entity_type(label)})
    return list(found.values())


def build_graph(conn, *, source=None, project=None, since=None, until=None,
                entity_type=None, min_edge_weight=1, max_nodes=100):
    """Build an entity co-occurrence graph from filtered indexed messages."""
    where = ["COALESCE(nl_text, '') != ''"]
    params = []
    if source:
        where.append("source = ?")
        params.append({"claude": "claude-code"}.get(source, source))
    if project:
        where.append("project LIKE ?")
        params.append(f"%{project}%")
    if since is not None:
        where.append("epoch >= ?")
        params.append(since)
    if until is not None:
        where.append("epoch <= ?")
        params.append(until)
    rows = conn.execute(
        "SELECT id,session_id,source,path,line_no,ts,nl_text FROM messages WHERE "
        + " AND ".join(where) + " ORDER BY id", params).fetchall()

    nodes, mentions = {}, Counter()
    references = defaultdict(list)
    messages = []
    for row in rows:
        entities = extract_entities(row["nl_text"])
        if entity_type:
            entities = [entity for entity in entities if entity["type"] == entity_type]
        if len(entities) < 1:
            continue
        ids = sorted({entity["id"] for entity in entities})
        ref = {"message_id": row["id"], "session_id": row["session_id"],
               "source": row["source"], "path": row["path"],
               "line_no": row["line_no"], "timestamp": row["ts"]}
        messages.append((ids, ref))
        for entity in entities:
            nodes.setdefault(entity["id"], entity)
            mentions[entity["id"]] += 1
            references[entity["id"]].append(ref)

    keep = {key for key, _ in sorted(mentions.items(), key=lambda item: (-item[1], item[0]))[:max_nodes]}
    edge_counts, edge_refs = Counter(), defaultdict(list)
    for ids, ref in messages:
        ids = [key for key in ids if key in keep]
        for pair in combinations(ids, 2):
            edge_counts[pair] += 1
            edge_refs[pair].append(ref)

    output_nodes = [{**nodes[key], "mentions": mentions[key], "references": references[key]}
                    for key in sorted(keep)]
    output_edges = [{"source": pair[0], "target": pair[1], "weight": weight,
                     "references": edge_refs[pair]}
                    for pair, weight in sorted(edge_counts.items()) if weight >= min_edge_weight]
    return {"nodes": output_nodes, "edges": output_edges,
            "meta": {"messages_scanned": len(rows), "max_nodes": max_nodes,
                     "min_edge_weight": min_edge_weight}}


def render_graph(graph, output_format="json") -> str:
    if output_format == "json":
        return json.dumps(graph, indent=2, ensure_ascii=False) + "\n"
    lines = ["graph recall {"]
    for node in graph["nodes"]:
        label = node["label"].replace('"', r'\"')
        lines.append(f'  "{node["id"]}" [label="{label} ({node["mentions"]})"];')
    for edge in graph["edges"]:
        lines.append(f'  "{edge["source"]}" -- "{edge["target"]}" [weight={edge["weight"]}, label="{edge["weight"]}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
