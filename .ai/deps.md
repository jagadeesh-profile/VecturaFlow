# Dependency Pinning + Doc Links

> Pinned versions and canonical docs. Query here before guessing or web-searching.
> Update when upgrading a dependency. Never upgrade without updating this file.

## Core API
- FastAPI 0.111.0 → https://fastapi.tiangolo.com/
- Pydantic v2 2.12.5 → https://docs.pydantic.dev/latest/ (use model_dump(), not dict())
- pydantic-settings 2.13.1 → https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- uvicorn 0.29.0 → https://www.uvicorn.org/

## LLM / Agent
- LangGraph 0.1.1 → https://langchain-ai.github.io/langgraph/ (StateGraph pattern, TypedDict state)
- LangChain 0.2.1 → https://python.langchain.com/docs/ (import from langchain_openai, NOT langchain.chat_models)
- langchain-openai 0.1.8 → https://python.langchain.com/docs/integrations/platforms/openai/
- openai 1.30.1 → https://platform.openai.com/docs/api-reference

## Embeddings / Vector
- text-embedding-3-small → 1536-dim, cosine metric, max 8191 tokens input
- Pinecone 3.2.2 → https://docs.pinecone.io/reference/ (serverless, us-east-1, cosine)

## AWS
- boto3 1.34.102 → https://boto3.amazonaws.com/v1/documentation/api/latest/index.html
- botocore 1.34.102 → same as boto3

## Document Parsing
- PyMuPDF 1.27.2.2 → https://pymupdf.readthedocs.io/ (PDF parser)
- python-docx 1.2.0 → https://python-docx.readthedocs.io/ (DOCX parser)
- unstructured[docx] 0.21.5 → https://unstructured-io.github.io/unstructured/
- pandas 2.3.3 → https://pandas.pydata.org/docs/ (CSV/Excel parser)

## Observability
- structlog 24.1.0 → https://www.structlog.org/en/stable/ (JSON in prod, console in dev)
- prometheus-client 0.20.0 → https://github.com/prometheus/client_python

## Caching
- redis 5.0.4 → https://redis-py.readthedocs.io/ (5-min TTL, bypass on failure)

## Testing
- pytest 8.2.0 → https://docs.pytest.org/
- moto 5.0.6 → https://docs.getmoto.org/ (mock S3, SQS, DynamoDB — use @mock_aws)
- pytest-asyncio 0.23.6 → https://pytest-asyncio.readthedocs.io/

## Watch List (packages with known volatility)
- pinecone-client 3.2.2 — v4 API breaking changes expected; check changelog before upgrading
- langgraph 0.1.1 — rapid iteration; check changelog before upgrading
- langchain 0.2.1 — never import from langchain.chat_models; use langchain_openai
