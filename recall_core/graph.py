"""Deterministic, dependency-free knowledge graph generation.

Entity extraction is deliberately precise rather than exhaustive: it recognizes
a small set of token classes that are reliable in coding transcripts (mentions,
hashtags, issue references, domains, file paths, and a curated technology
gazetteer) instead of treating every capitalized word as an entity, which was
extremely noisy. Optional spaCy NER can be layered on top when the model is
installed, mirroring the opt-in ``--semantic`` search path.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
import re


# --------------------------------------------------------------------------- #
# Deterministic regex + gazetteer extraction (no dependencies)
# --------------------------------------------------------------------------- #

_STOP = {
    "a", "an", "and", "assistant", "but", "can", "could", "do", "for", "from",
    "here", "how", "i", "if", "in", "it", "let", "please", "the", "then", "this",
    "to", "use", "user", "we", "what", "when", "where", "why", "you",
}

# Distinctive technology names matched case-insensitively with word boundaries.
# Ambiguous names whose lowercase form is a common English word (React, Go,
# Rust, Swift, Ruby, Julia, Express, Node.js, Next.js) live in _CASE_TECH below
# and are matched case-sensitively instead.
_TECH_TERMS = frozenset({
    "airflow", "angular", "anthropic", "astro", "aws", "azure", "babel",
    "bash", "claude", "cloudflare", "codex", "dbt", "docker", "django",
    "drizzle-team", "elasticsearch", "emnapi", "esbuild", "esbuild-kit",
    "eslint", "eslint-community", "fastapi", "flask", "flutter", "git",
    "github", "gitlab", "graphql", "grpc", "helm", "humanfs", "java",
    "javascript", "kafka", "kotlin", "kubernetes", "k8s", "langchain",
    "langgraph", "linux", "llm", "mongodb", "mysql", "nextjs", "numpy",
    "openai", "opentelemetry", "oxc", "oxc-project", "pandas", "poppinss",
    "postgres", "postgresql", "prisma", "pytorch", "rabbitmq", "redis",
    "resvg", "rolldown", "snowflake", "spark", "speed-highlight", "sqlite",
    "svelte", "tailwind", "tailwindcss", "tensorflow", "terraform",
    "typescript", "typescript-eslint", "unpic", "vercel", "vite", "vitejs",
    "vue", "webassembly", "webassemblyjs", "webpack",
})

_CASE_TECH = ("React", "Go", "Rust", "Swift", "Ruby", "Julia", "Express",
              "Node.js", "Next.js")

_TECH_RE = re.compile(
    r"(?<![\w@#])(?:" + "|".join(sorted(_TECH_TERMS, key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE,
)
_CASE_TECH_RE = re.compile(
    r"(?<![\w@#])(?:" + "|".join(re.escape(t) for t in _CASE_TECH) + r")(?![\w])"
)

# ``@`` tokens that are not people: CSS at-rules, JSDoc/TSDoc tags, decorators,
# annotations, and other code-only tokens seen in transcripts.
_AT_NOISE = frozenset({
    # CSS at-rules
    "charset", "container", "font-face", "import", "keyframes", "layer",
    "media", "page", "property", "supports", "tailwind", "apply", "screen",
    "theme",
    # JSDoc / TSDoc tags
    "param", "returns", "return", "type", "typedef", "callback", "interface",
    "see", "link", "throws", "example", "deprecated", "inheritdoc", "abstract",
    "implements", "augments", "public", "private", "protected", "readonly",
    "static", "async", "generator", "constructor", "class", "namespace",
    "enum", "extends", "override", "since", "version", "author", "default",
    # decorators / annotations
    "staticmethod", "classmethod", "dataclass", "abstractmethod", "pytest",
    "inject", "injectable", "component", "directive", "pipe", "viewchild",
    "hostlistener", "hostbinding", "autowired", "repository", "service",
    "controller", "requestmapping", "getmapping", "postmapping",
    "configuration", "value", "mock", "spy", "beforeeach", "aftereach",
    "beforeall", "afterall", "test", "describe", "jest", "vitest", "spyon",
    "contextmanager", "lru_cache", "wraps", "decorator", "singledispatch",
    "memo", "mixin", "resource", "bean", "cache",
    # placeholders / misc observed in transcripts
    "foo", "bar", "baz", "localhost", "mentions", "alloc", "cf", "cmd8",
    "file", "owner", "sidecar", "tests", "img", "example", "username", "user",
    "self",
})

_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][\w.-]*)")
_HASHTAG_RE = re.compile(r"(?<![\w#])#([A-Za-z0-9][\w.-]*)")
_ISSUE_RE = re.compile(r"(?<!\w)(?:gh-|GH-)(\d{1,7})(?!\w)")

_HEX_COLOR_RE = re.compile(r"^(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _is_hex_color(tag: str) -> bool:
    """True for CSS hex colors such as ``0a0a0a`` or ``1A1A1A`` (not topics)."""
    return bool(_HEX_COLOR_RE.match(tag))

_TLDS = r"com|org|net|io|ai|dev|co|app|sh|xyz|me|ly|so|gg|info"
_DOMAIN_RE = re.compile(
    rf"(?<![\w@#])(?:[A-Za-z0-9-]+\.)+(?:{_TLDS})(?![\w])"
)

_FILE_EXT = (
    r"pyi?|jsx?|tsx?|mjs|cjs|go|rs|rb|java|c|cc|cpp|cxx|h|hpp|cs|php|swift|kt|kts|"
    r"scala|sh|bash|zsh|ya?ml|json|toml|ini|cfg|conf|md|markdown|rst|txt|css|scss|"
    r"less|html?|vue|svelte|astro|sql|graphql|gql|proto|tf|tfvars|lock|env|prisma|"
    r"nix|dart|ex|exs"
)
_FILE_RE = re.compile(
    rf"(?<![\w@#])(?:[A-Za-z0-9_.@-]+/)*[A-Za-z0-9_.@-]+\.(?:{_FILE_EXT})(?![\w])"
)


def _entity_id(value: str, keep_separators: bool = False) -> str:
    """Normalize an entity label into a stable node id.

    ``keep_separators`` preserves ``/`` and ``.`` for file paths so distinct
    paths do not collide after punctuation stripping.
    """
    value = value.lstrip("#@").lower().rstrip(".,;:!?()[]{}\"' ")
    if keep_separators:
        return value
    value = re.sub(rf"\.(?:{_TLDS})$", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _is_domain_segment(value: str) -> bool:
    """Return True if a file-path candidate is really a URL (segment is a TLD)."""
    return any(
        re.search(rf"\.(?:{_TLDS})$", segment)
        for segment in value.split("/")
    )


def extract_entities(text: str) -> list[dict[str, str]]:
    """Extract precise, deterministic entity candidates without any dependency."""
    if not text:
        return []
    found = {}

    def add(value: str, entity_type: str, keep_separators: bool = False):
        entity_id = _entity_id(value, keep_separators=keep_separators)
        if entity_id:
            found.setdefault(entity_id, {"id": entity_id, "label": value, "type": entity_type})

    for match in _MENTION_RE.finditer(text):
        handle = match.group(1)
        if text[match.end():match.end() + 1] == "/":
            add("@" + handle, "organization")   # npm scoped package: @scope/name
            continue
        if handle in _AT_NOISE:
            continue
        if "." in handle:
            if re.search(rf"\.(?:{_FILE_EXT})$", handle):
                add(handle, "file", keep_separators=True)   # @file.ext reference
            continue   # hostname / email-like token
        if handle.lower() in _TECH_TERMS or handle in _CASE_TECH:
            add(handle, "technology")   # known tech/org name, not a person
            continue
        add("@" + handle, "person")
    for match in _HASHTAG_RE.finditer(text):
        tag = match.group(1).rstrip(".-")
        if not tag or _is_hex_color(tag):
            continue
        add("#" + tag, "reference" if tag.isdigit() else "topic")
    for match in _ISSUE_RE.finditer(text):
        add("gh-" + match.group(1), "reference")
    for match in _DOMAIN_RE.finditer(text):
        add(match.group(0), "organization")
    for match in _FILE_RE.finditer(text):
        if not _is_domain_segment(match.group(0)):
            add(match.group(0), "file", keep_separators=True)
    for match in _TECH_RE.finditer(text):
        add(match.group(0), "technology")
    for match in _CASE_TECH_RE.finditer(text):
        add(match.group(0), "technology")
    return list(found.values())


# --------------------------------------------------------------------------- #
# Optional spaCy NER (opt-in, mirrors --semantic)
# --------------------------------------------------------------------------- #

_NLP = None   # cache the pipeline so repeated graph builds reuse it

_NER_TYPES = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "entity",
    "LOC": "entity",
    "FAC": "entity",
    "NORP": "entity",
    "PRODUCT": "entity",
    "EVENT": "entity",
    "WORK_OF_ART": "entity",
    "LANGUAGE": "entity",
}


class NerUnavailable(RuntimeError):
    pass


def _ner_dependency_error():
    try:
        import spacy  # noqa: F401
    except ImportError:
        return "ner mode needs spacy:\n    pip install spacy && python -m spacy download en_core_web_sm"
    return None


def _load_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP
    error = _ner_dependency_error()
    if error:
        raise NerUnavailable(error)
    import spacy
    try:
        _NLP = spacy.load("en_core_web_sm", disable=["tagger", "parser", "lemmatizer"])
    except OSError:
        raise NerUnavailable(
            "ner mode needs the spaCy model:\n    python -m spacy download en_core_web_sm"
        )
    return _NLP


def extract_entities_ner(text: str, nlp) -> list[dict[str, str]]:
    """Extract named entities from prose with a spaCy pipeline.

    Stock NER recognizes people, organizations, and places reliably, but is not
    a dependable source of technology names (it tags some inconsistently and
    misses ambiguous ones), so it supplements — never replaces — the gazetteer.
    """
    if not text or not text.strip():
        return []
    found = {}
    try:
        doc = nlp(text)
    except Exception:
        return []
    for ent in doc.ents:
        label = ent.text.strip()
        entity_id = _entity_id(label)
        if len(entity_id) < 2 or entity_id in _STOP:
            continue
        entity_type = _NER_TYPES.get(ent.label_, "entity")
        found.setdefault(entity_id, {"id": entity_id, "label": label, "type": entity_type})
    return list(found.values())


# --------------------------------------------------------------------------- #
# Graph construction and rendering
# --------------------------------------------------------------------------- #

def build_graph(conn, *, source=None, project=None, since=None, until=None,
                entity_type=None, min_edge_weight=1, max_nodes=100, ner=False):
    """Build an entity co-occurrence graph from filtered indexed messages."""
    nlp = _load_nlp() if ner else None
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
        if nlp is not None:
            seen = {entity["id"] for entity in entities}
            for entity in extract_entities_ner(row["nl_text"], nlp):
                if entity["id"] not in seen:
                    entities.append(entity)
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
    if output_format == "html":
        from . import graph_html
        return graph_html.render_html(graph)
    lines = ["graph recall {"]
    for node in graph["nodes"]:
        label = node["label"].replace('"', r'\"')
        lines.append(f'  "{node["id"]}" [label="{label} ({node["mentions"]})"];')
    for edge in graph["edges"]:
        lines.append(f'  "{edge["source"]}" -- "{edge["target"]}" [weight={edge["weight"]}, label="{edge["weight"]}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
