# agents.md — The 11 Specialist Agents

VecturaFlow's logic is split across eleven named "agents" (really, focused
modules). Each agent owns a specific slice of the codebase. Before you touch
code, find the agent that owns those files and read the matching markdown brief
in `.claude/agents/<name>.md`.

| # | Agent                   | Brief file                                      | Owned source                                       | Domain                                              |
|---|-------------------------|-------------------------------------------------|----------------------------------------------------|-----------------------------------------------------|
| 1 | FileIngestionAgent      | `.claude/agents/file-ingestion-agent.md`        | `ingestion/lambda_s3.py`                           | S3 `ObjectCreated:*` → SQS ingest queue.            |
| 2 | ParserAgent             | `.claude/agents/parser-agent.md`                | `ingestion/parser.py`                              | PDF / DOCX / CSV / TXT / JSON → `TextBlock[]`.      |
| 3 | ChunkingAgent           | `.claude/agents/chunking-agent.md`              | `ingestion/chunker.py`, `ingestion/models.py`      | Per-block chunking (metadata-preserving).           |
| 4 | WebhookIngestionAgent   | `.claude/agents/webhook-ingestion-agent.md`     | `ingestion/lambda_webhook.py`                      | API Gateway webhook → registry + SQS.               |
| 5 | EmbeddingAgent          | `.claude/agents/embedding-agent.md`             | `embeddings/lambda_embed.py`                       | SQS embed queue → OpenAI → Pinecone upsert.         |
| 6 | RetrieverAgent          | `.claude/agents/retriever-agent.md`             | `api/retriever.py`                                 | Query embed + Pinecone MMR search + Redis cache.    |
| 7 | RAGAgent                | `.claude/agents/rag-agent.md`                   | `api/agent.py`                                     | LangGraph 4-node: decompose→retrieve→generate→validate. |
| 8 | QueryHandlerAgent       | `.claude/agents/query-handler-agent.md`         | `api/main.py`, `api/schemas.py`, `api/dependencies.py`, `api/rate_limit.py` | FastAPI routes, auth, rate limit. |
| 9 | InfraDeployAgent        | `.claude/agents/infra-deploy-agent.md`          | `infra/`, `Dockerfile`, `.github/workflows/*`      | Terraform, Docker, CI/CD, ECS.                      |
| 10| TestAgent               | `.claude/agents/test-agent.md`                  | `tests/`                                           | moto-backed pytest suite (107 tests, 82%).          |
| 11| DemoAgent               | `.claude/agents/demo-agent.md`                  | `scripts/demo.py`                                  | End-to-end Loom-ready walkthrough.                  |

---

## Rule of thumb

1. **Find the agent** that owns the file you're about to change.
2. **Read its brief** — every agent has a workflow and a list of things
   *not* to do.
3. **Respect domain boundaries.** If a fix needs to cross two agents
   (e.g. schema change in `api/schemas.py` that affects `api/agent.py`),
   update the brief of each agent that cares.

---

## Shared modules (no single agent owns)

| Module                         | Used by                                    |
|--------------------------------|--------------------------------------------|
| `api/config.py`                | Everyone — settings singleton.             |
| `api/logger.py`                | Everyone in `api/` — structlog wrapper.    |
| `ingestion/logging_util.py`    | Every Lambda — structlog wrapper.          |
| `api/schemas.py`               | main, agent, rate_limit, dependencies.     |
| `api/observability.py`         | main (middleware + `/metrics`), retriever. |
| `ingestion/models.py`          | parser, chunker, lambda_parser.            |

Shared modules changes touch **many agents** — coordinate before editing.
