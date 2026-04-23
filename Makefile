.PHONY: install setup-aws setup-pinecone dev lambda-image-push test lint clean graphify graphify-check verify verify-q preflight triage triage-apply pinecone-stats check-all help

# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────

install: ## Install Python dependencies
	@echo "Installing dependencies..."
	pip install -r requirements.txt
	pip install ruff

validate: ## Validate environment and connections
	@echo "Validating environment and connections..."
	python scripts/validate_env.py

validate-local: ## Validate locally, skipping AWS and Pinecone
	@echo "Validating locally (skip AWS + Pinecone)..."
	python scripts/validate_env.py --skip-aws --skip-pinecone

lint: ## Run ruff and auto-fix api/ and ingestion/
	@echo "Running ruff linter..."
	ruff check api/ ingestion/ --fix

lint-check: ## Run ruff without fixing
	@echo "Checking code style (no fixes)..."
	ruff check api/ ingestion/

setup-aws: ## Provision AWS resources
	@echo "Provisioning AWS resources..."
	python -m scripts.setup_aws

setup-aws-dry: ## Dry-run AWS setup
	@echo "Dry-run AWS setup..."
	python -m scripts.setup_aws --dry-run

setup-pinecone: ## Set up Pinecone index
	@echo "Setting up Pinecone index..."
	python -m scripts.setup_pinecone

setup-pinecone-dry: ## Dry-run Pinecone setup
	@echo "Dry-run Pinecone setup..."
	python -m scripts.setup_pinecone --dry-run

setup: install setup-aws setup-pinecone ## Full setup (install + aws + pinecone)
	@echo "Full setup complete."

# ─────────────────────────────────────────────────────────────────────────────
# Development
# ─────────────────────────────────────────────────────────────────────────────

dev: ## Start FastAPI dev server
	@echo "Starting VecturaFlow API in dev mode..."
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

lambda-image-push: ## Build and push Lambda image: make lambda-image-push IMAGE=<ecr-uri:tag>
	@test -n "$(IMAGE)" || (echo "IMAGE is required, e.g. make lambda-image-push IMAGE=..."; exit 1)
	docker buildx build --platform linux/amd64 --provenance=false --sbom=false -f Dockerfile.lambda -t "$(IMAGE)" --push .

# ─────────────────────────────────────────────────────────────────────────────
# Graphify — regenerate portable AI-agent memory
# ─────────────────────────────────────────────────────────────────────────────

graphify: ## Refresh graphify memory files
	@echo "Refreshing graphify/modules and graphify/graph.json..."
	python scripts/graphify.py

graphify-check: ## Check graphify is up to date
	@echo "Checking graphify is up to date (fails if drift)..."
	python scripts/graphify.py --dry-run | tee /tmp/graphify.out
	@grep -q 'would change 0 file' /tmp/graphify.out || (echo "graphify is stale — run 'make graphify'" && exit 1)

# ─────────────────────────────────────────────────────────────────────────────
# Testing
# ─────────────────────────────────────────────────────────────────────────────

test: ## Run the test suite
	pytest tests/ -v

test-all: ## Run the full test suite with short tracebacks
	pytest tests/ -v --tb=short

test-fast: ## Fail fast with terse output
	pytest tests/ -q -x --tb=line

test-cov: ## Run tests with coverage report
	pytest tests/ --cov=api --cov=ingestion --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

verify: ## Run full RAG end-to-end verification with default questions
	@echo "Running full RAG end-to-end verification..."
	python scripts/verify_end_to_end.py

verify-q: ## Run verification with a custom question: make verify-q Q="your question"
	@echo "Running RAG verification with a custom question..."
	python scripts/verify_end_to_end.py "$(Q)"

preflight: ## Check OpenAI key is valid and not a placeholder
	@echo "Running OpenAI preflight check..."
	python scripts/preflight.py

triage: ## Dry-run queue triage
	@echo "Dry-run queue triage..."
	python scripts/triage_queue.py

triage-apply: ## Apply queue triage decisions (drain/reprocess/DLQ)
	@echo "Applying queue triage decisions..."
	python scripts/triage_queue.py --apply

pinecone-stats: ## Show Pinecone index stats and sample vectors
	@echo "Showing Pinecone index stats and sample vectors..."
	python scripts/verify_pinecone.py

check-all: preflight pinecone-stats verify ## Full health check in order
	@echo "Full health check complete."

# ─────────────────────────────────────────────────────────────────────────────
# Smoke test against running server
# ─────────────────────────────────────────────────────────────────────────────

smoke: ## Smoke test running server
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

clean: ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .coverage

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo "For failure diagnosis, see docs/TROUBLESHOOTING.md (or the Health Check section in README.md)."

# ─────────────────────────────────────────────────────────────────────────────
# POC
# ─────────────────────────────────────────────────────────────────────────────

poc: ## Run the full POC validation
	@echo "Running VecturaFlow POC validation..."
	python poc/poc_runner.py

poc-local: ## Run the POC without AWS-dependent tests
	@echo "Running POC (skip AWS tests)..."
	python poc/poc_runner.py --skip-aws

poc-002: ## Run POC test 002
	python poc/poc_runner.py --test poc002

poc-003: ## Run POC test 003
	python poc/poc_runner.py --test poc003

poc-004: ## Run POC test 004
	python poc/poc_runner.py --test poc004

poc-005: ## Run POC test 005
	python poc/poc_runner.py --test poc005
