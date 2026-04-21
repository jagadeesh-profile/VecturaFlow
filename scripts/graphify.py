"""
VecturaFlow — graphify regenerator.

Walks the live source tree and refreshes:
  graphify/modules/*.md   — one markdown card per source file
  graphify/graph.json     — machine-readable node + edge manifest

The hand-written memory files (INDEX.md, architecture.md, agents.md,
dataflow.md, glossary.md, decisions.md) are NEVER touched by this script.
Those encode intent and are edited by humans.

Usage:
    python scripts/graphify.py                # regenerate everything
    python scripts/graphify.py --dry-run      # print what would change

Design rules:
    • Stdlib-only. No deps. Must run in any environment.
    • Parse imports with `ast` — never regex. Comments-in-strings break regex.
    • Idempotent. Running twice produces identical bytes.
    • Deterministic. Nodes and edges are sorted before write.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import sys
from typing import Any

# ─── Config ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPHIFY_DIR = REPO_ROOT / "graphify"
MODULES_DIR = GRAPHIFY_DIR / "modules"
GRAPH_JSON = GRAPHIFY_DIR / "graph.json"

# Packages we treat as first-party (edges to other first-party modules become graph edges)
FIRST_PARTY = {"api", "ingestion", "embeddings"}

# Packages worth tracking as external dependency nodes
TRACKED_EXTERNAL = {
    "fastapi", "pydantic", "pydantic_settings", "structlog",
    "boto3", "botocore", "openai", "pinecone", "redis",
    "langchain", "langchain_core", "langchain_openai", "langgraph",
    "prometheus_client", "uvicorn",
}

# Source directories to scan for modules
SRC_DIRS = ["api", "ingestion", "embeddings", "scripts"]

# Files to skip entirely
SKIP_FILES = {"__init__.py"}

# AWS / external resources referenced by setting / env names — hand-coded because
# static analysis can't reliably discover them from boto3 call sites.
KNOWN_RESOURCES = [
    {"id": "aws:s3:ingestion_bucket", "kind": "aws", "service": "s3",
     "name": "INGESTION_BUCKET", "notes": "Raw uploads land here; fires lambda_s3."},
    {"id": "aws:sqs:ingest_queue", "kind": "aws", "service": "sqs",
     "name": "INGESTION_QUEUE_URL", "notes": "Between lambda_s3 and lambda_parser."},
    {"id": "aws:sqs:embed_queue", "kind": "aws", "service": "sqs",
     "name": "EMBEDDING_QUEUE_URL", "notes": "Between lambda_parser and lambda_embed."},
    {"id": "aws:dynamodb:registry", "kind": "aws", "service": "dynamodb",
     "name": "vecturaflow-registry", "notes": "PK doc_id; GSI status-ingested_at-index."},
    {"id": "aws:dynamodb:keys", "kind": "aws", "service": "dynamodb",
     "name": "vecturaflow-keys", "notes": "PK api_key."},
    {"id": "pinecone:index", "kind": "external", "service": "pinecone",
     "name": "vecturaflow", "notes": "1536-dim cosine; us-east-1."},
    {"id": "redis:cache", "kind": "external", "service": "redis",
     "name": "retrieval-cache", "notes": "5-min TTL."},
    {"id": "openai:embeddings", "kind": "external", "service": "openai",
     "name": "text-embedding-3-small", "notes": "1536-dim."},
    {"id": "openai:generation", "kind": "external", "service": "openai",
     "name": "gpt-4o-mini", "notes": "temperature=0."},
]

# Hand-curated agent-to-module ownership (lives in graphify/agents.md for humans;
# duplicated here so graph.json carries the edge set)
AGENT_OWNERSHIP = {
    "FileIngestionAgent":    ["ingestion/lambda_s3.py"],
    "ParserAgent":           ["ingestion/parser.py"],
    "ChunkingAgent":         ["ingestion/chunker.py", "ingestion/models.py"],
    "WebhookIngestionAgent": ["ingestion/lambda_webhook.py"],
    "EmbeddingAgent":        ["embeddings/lambda_embed.py"],
    "RetrieverAgent":        ["api/retriever.py"],
    "RAGAgent":              ["api/agent.py"],
    "QueryHandlerAgent":     ["api/main.py", "api/schemas.py",
                              "api/dependencies.py", "api/rate_limit.py"],
    "InfraDeployAgent":      [],
    "TestAgent":             [],
    "DemoAgent":             ["scripts/demo.py"],
}


# ─── AST parsing ─────────────────────────────────────────────────────────────

class ModuleInfo:
    """Facts extracted from one Python source file."""

    __slots__ = ("path", "rel_path", "module_id", "docstring", "imports",
                 "first_party_deps", "external_deps", "functions", "classes")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel_path = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        # module_id = "api/retriever.py" → "api.retriever"
        self.module_id = self.rel_path.removesuffix(".py").replace("/", ".")
        self.docstring = ""
        self.imports: list[str] = []
        self.first_party_deps: list[str] = []
        self.external_deps: list[str] = []
        self.functions: list[str] = []
        self.classes: list[str] = []


def parse_module(path: Path) -> ModuleInfo:
    info = ModuleInfo(path)
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"warning: could not parse {path}: {exc}", file=sys.stderr)
        return info

    info.docstring = (ast.get_docstring(tree) or "").strip()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                info.imports.append(node.module)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                info.functions.append(node.name)
        elif isinstance(node, ast.ClassDef):
            info.classes.append(node.name)

    seen_fp: set[str] = set()
    seen_ext: set[str] = set()
    for imp in info.imports:
        root = imp.split(".", 1)[0]
        if root in FIRST_PARTY and imp not in seen_fp:
            info.first_party_deps.append(imp)
            seen_fp.add(imp)
        elif root in TRACKED_EXTERNAL and root not in seen_ext:
            info.external_deps.append(root)
            seen_ext.add(root)

    info.first_party_deps.sort()
    info.external_deps.sort()
    info.functions.sort()
    info.classes.sort()
    return info


def discover_modules() -> list[ModuleInfo]:
    modules: list[ModuleInfo] = []
    for src in SRC_DIRS:
        root = REPO_ROOT / src
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.name in SKIP_FILES:
                continue
            modules.append(parse_module(path))
    return modules


# ─── Markdown card rendering ─────────────────────────────────────────────────

def card_filename(info: ModuleInfo) -> str:
    """api/retriever.py  →  api-retriever.md"""
    return info.rel_path.removesuffix(".py").replace("/", "-") + ".md"


def render_card(info: ModuleInfo, agents_owning: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"# `{info.rel_path}`")
    lines.append("")
    lines.append(f"**Module id:** `{info.module_id}`")
    lines.append("")

    if agents_owning:
        lines.append(f"**Owned by:** {', '.join(f'`{a}`' for a in agents_owning)}")
    else:
        lines.append("**Owned by:** _shared / unowned_")
    lines.append("")

    if info.docstring:
        # Keep only the first paragraph for the card summary
        summary = info.docstring.split("\n\n", 1)[0].strip()
        lines.append("## Purpose")
        lines.append("")
        lines.append(summary)
        lines.append("")

    if info.first_party_deps:
        lines.append("## First-party imports")
        lines.append("")
        for dep in info.first_party_deps:
            lines.append(f"- `{dep}`")
        lines.append("")

    if info.external_deps:
        lines.append("## External dependencies")
        lines.append("")
        lines.append(", ".join(f"`{d}`" for d in info.external_deps))
        lines.append("")

    if info.classes:
        lines.append("## Classes")
        lines.append("")
        for c in info.classes:
            lines.append(f"- `{c}`")
        lines.append("")

    if info.functions:
        lines.append("## Public functions")
        lines.append("")
        for f in info.functions:
            lines.append(f"- `{f}()`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "_Regenerated by `scripts/graphify.py`. Do not hand-edit — "
        "changes will be overwritten._"
    )
    lines.append("")
    return "\n".join(lines)


# ─── Graph manifest ──────────────────────────────────────────────────────────

def build_graph(modules: list[ModuleInfo]) -> dict[str, Any]:
    # Ownership lookup: rel_path → [agent_name, ...]
    owner_index: dict[str, list[str]] = {}
    for agent, paths in AGENT_OWNERSHIP.items():
        for p in paths:
            owner_index.setdefault(p, []).append(agent)

    nodes: list[dict[str, Any]] = []

    # 1. module nodes
    for m in modules:
        nodes.append({
            "id": m.module_id,
            "kind": "module",
            "path": m.rel_path,
            "owned_by": sorted(owner_index.get(m.rel_path, [])),
            "classes": m.classes,
            "functions": m.functions,
            "external_deps": m.external_deps,
        })

    # 2. agent nodes
    for agent in sorted(AGENT_OWNERSHIP):
        nodes.append({
            "id": f"agent:{agent}",
            "kind": "agent",
            "name": agent,
            "brief": f".claude/agents/{_agent_brief_name(agent)}.md",
            "owns": sorted(AGENT_OWNERSHIP[agent]),
        })

    # 3. resource nodes
    nodes.extend(KNOWN_RESOURCES)

    # Edges
    edges: list[dict[str, str]] = []
    module_ids = {m.module_id for m in modules}

    for m in modules:
        for dep in m.first_party_deps:
            if dep in module_ids:
                edges.append({"from": m.module_id, "to": dep, "kind": "imports"})
        for dep in m.external_deps:
            edges.append({"from": m.module_id, "to": f"ext:{dep}",
                          "kind": "depends_on"})

    for agent, paths in AGENT_OWNERSHIP.items():
        for p in paths:
            mod_id = p.removesuffix(".py").replace("/", ".")
            if mod_id in module_ids:
                edges.append({"from": f"agent:{agent}", "to": mod_id,
                              "kind": "owns"})

    # Data-flow edges (hand-encoded — cannot be inferred statically)
    edges.extend([
        {"from": "ingestion.lambda_s3", "to": "aws:sqs:ingest_queue", "kind": "publishes_to"},
        {"from": "ingestion.lambda_webhook", "to": "aws:sqs:ingest_queue", "kind": "publishes_to"},
        {"from": "ingestion.lambda_parser", "to": "aws:sqs:ingest_queue", "kind": "consumes_from"},
        {"from": "ingestion.lambda_parser", "to": "aws:sqs:embed_queue", "kind": "publishes_to"},
        {"from": "embeddings.lambda_embed", "to": "aws:sqs:embed_queue", "kind": "consumes_from"},
        {"from": "embeddings.lambda_embed", "to": "pinecone:index", "kind": "writes"},
        {"from": "embeddings.lambda_embed", "to": "openai:embeddings", "kind": "calls"},
        {"from": "ingestion.lambda_s3", "to": "aws:dynamodb:registry", "kind": "writes"},
        {"from": "ingestion.lambda_webhook", "to": "aws:dynamodb:registry", "kind": "writes"},
        {"from": "ingestion.lambda_parser", "to": "aws:dynamodb:registry", "kind": "writes"},
        {"from": "embeddings.lambda_embed", "to": "aws:dynamodb:registry", "kind": "writes"},
        {"from": "api.dependencies", "to": "aws:dynamodb:keys", "kind": "reads"},
        {"from": "api.retriever", "to": "pinecone:index", "kind": "reads"},
        {"from": "api.retriever", "to": "openai:embeddings", "kind": "calls"},
        {"from": "api.retriever", "to": "redis:cache", "kind": "reads_writes"},
        {"from": "api.agent", "to": "openai:generation", "kind": "calls"},
    ])

    # Sort for deterministic output
    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["from"], e["to"], e["kind"]))

    return {
        "version": 1,
        "repo": "VecturaFlow",
        "generator": "scripts/graphify.py",
        "nodes": nodes,
        "edges": edges,
    }


def _agent_brief_name(agent: str) -> str:
    """RAGAgent → rag-agent"""
    # "FileIngestionAgent" → "file-ingestion-agent"
    import re
    # split before each uppercase letter (not the first), then lowercase + join with -
    parts = re.findall(r"[A-Z][a-z0-9]*", agent)
    return "-".join(p.lower() for p in parts) if parts else agent.lower()


# ─── Writer ──────────────────────────────────────────────────────────────────

def write_if_changed(path: Path, content: str, dry_run: bool = False) -> bool:
    """Return True if content differs from on-disk."""
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    if dry_run:
        print(f"would write {path.relative_to(REPO_ROOT)} ({len(content)} bytes)")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without writing.")
    args = parser.parse_args()

    modules = discover_modules()
    if not modules:
        print("no modules found — check SRC_DIRS", file=sys.stderr)
        return 1

    owner_index: dict[str, list[str]] = {}
    for agent, paths in AGENT_OWNERSHIP.items():
        for p in paths:
            owner_index.setdefault(p, []).append(agent)

    # Wipe stale cards (any .md in modules/ not in current module set).
    # Cleanest way to avoid drift when a file is renamed or deleted.
    expected_cards = {card_filename(m) for m in modules}
    if MODULES_DIR.exists():
        for existing in MODULES_DIR.glob("*.md"):
            if existing.name not in expected_cards:
                if args.dry_run:
                    print(f"would delete {existing.relative_to(REPO_ROOT)}")
                else:
                    existing.unlink()

    # Write cards
    changed = 0
    for m in modules:
        card = render_card(m, sorted(owner_index.get(m.rel_path, [])))
        if write_if_changed(MODULES_DIR / card_filename(m), card, args.dry_run):
            changed += 1

    # Write graph.json
    graph = build_graph(modules)
    graph_text = json.dumps(graph, indent=2, sort_keys=False) + "\n"
    if write_if_changed(GRAPH_JSON, graph_text, args.dry_run):
        changed += 1

    verb = "would change" if args.dry_run else "refreshed"
    print(f"graphify: {verb} {changed} file(s); "
          f"{len(modules)} module cards; "
          f"{len(graph['nodes'])} nodes; {len(graph['edges'])} edges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
