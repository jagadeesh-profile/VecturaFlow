.PHONY: install setup-aws setup-pinecone dev test lint clean graphify graphify-check

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

install:
	@echo "Installing dependencies..."
	pip install -r requirements.txt
	pip install ruff

validate:
	@echo "Validating environment and connections..."
	python scripts/validate_env.py

validate-local:
	@echo "Validating locally (skip AWS + Pinecone)..."
	python scripts/validate_env.py --skip-aws --skip-pinecone

lint:
	@echo "Running ruff linter..."
	ruff check api/ ingestion/ --fix

lint-check:
	@echo "Checking code style (no fixes)..."
	ruff check api/ ingestion/

setup-aws:
	@echo "Provisioning AWS resources..."
	python -m scripts.setup_aws

setup-aws-dry:
	@echo "Dry-run AWS setup..."
	python -m scripts.setup_aws --dry-run

setup-pinecone:
	@echo "Setting up Pinecone index..."
	python -m scripts.setup_pinecone

setup-pinecone-dry:
	@echo "Dry-run Pinecone setup..."
	python -m scripts.setup_pinecone --dry-run

setup: install setup-aws setup-pinecone
	@echo "Full setup complete."

# ─────────────────────────────────────────────────────────────────────────────
# Development
# ─────────────────────────────────────────────────────────────────────────────

dev:
	@echo "Starting VecturaFlow API in dev mode..."
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# ─────────────────────────────────────────────────────────────────────────────
# Graphify — regenerate portable AI-agent memory
# ─────────────────────────────────────────────────────────────────────────────

graphify:
	@echo "Refreshing graphify/modules and graphify/graph.json..."
	python scripts/graphify.py

graphify-check:
	@echo "Checking graphify is up to date (fails if drift)..."
	python scripts/graphify.py --dry-run | tee /tmp/graphify.out
	@grep -q 'would change 0 file' /tmp/graphify.out || (echo "graphify is stale — run 'make graphify'" && exit 1)

# ─────────────────────────────────────────────────────────────────────────────
# Testing
# ─────────────────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v

test-all:
	pytest tests/ -v --tb=short

test-fast:
	pytest tests/ -q -x --tb=line

test-cov:
	pytest tests/ --cov=api --cov=ingestion --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

# ─────────────────────────────────────────────────────────────────────────────
# Smoke test against running server
# ─────────────────────────────────────────────────────────────────────────────

smoke:
	@echo "Running smoke test against localhost:8000..."
	@curl -sf http://localhost:8000/health | python -m json.tool
	@echo ""
	@curl -sf -X POST http://localhost:8000/v1/chat/completions \
		-H "Authorization: Bearer dev" \
		-H "Content-Type: application/json" \
		-d '{"messages": [{"role": "user", "content": "What is VecturaFlow?"}]}' \
		| python -m json.tool

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage

.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "VecturaFlow — Available commands:"
	@echo ""
	@echo "  make install          Install Python dependencies"
	@echo "  make setup-aws        Create S3, SQS, DynamoDB resources on AWS"
	@echo "  make setup-aws-dry    Dry-run AWS setup (no changes)"
	@echo "  make setup-pinecone   Create Pinecone index"
	@echo "  make setup            Full setup (install + aws + pinecone)"
	@echo "  make dev              Start FastAPI dev server (hot-reload)"
	@echo "  make test             Run all tests"
	@echo "  make test-fast        Fail fast, terse output"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make smoke            Smoke test running server"
	@echo "  make clean            Remove build artifacts"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# POC
# ─────────────────────────────────────────────────────────────────────────────

poc:
	@echo "Running VecturaFlow POC validation..."
	python poc/poc_runner.py

poc-local:
	@echo "Running POC (skip AWS tests)..."
	python poc/poc_runner.py --skip-aws

poc-002:
	python poc/poc_runner.py --test poc002

poc-003:
	python poc/poc_runner.py --test poc003

poc-004:
	python poc/poc_runner.py --test poc004

poc-005:
	python poc/poc_runner.py --test poc005
