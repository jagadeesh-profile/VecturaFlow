# Live Context (overwritten at every session end)

## Current Task
Implement strongly recommended hardening

## State
- Phase: complete, awaiting/including commit on main
- Files touched: API auth/config, Terraform ECS/ALB/DynamoDB/alarms/backend,
  docker-compose/local env docs, setup script, tests, graphify knowledge files
- Verification:
  - Focused hardening tests: `uv run --python 3.11 --with-requirements requirements.txt python -m pytest tests/test_infra_static.py tests/test_api.py::test_api_key_lookup_uses_sha256_hash tests/test_api.py::test_dev_key_requires_explicit_bypass_flag --tb=short --no-cov` -> 8 passed
  - Full suite: `uv run --python 3.11 --with-requirements requirements.txt --with pytest-cov python -m pytest tests/ --tb=short` -> 121 passed, 82.85% coverage, 1 Starlette/python_multipart warning
  - `python -m ruff check api ingestion embeddings scripts/setup_aws.py tests/test_infra_static.py tests/test_api.py tests/test_rate_limit.py tests/test_rag.py` -> pass
  - `terraform fmt -check -recursive infra/terraform` -> pass
  - `terraform validate` from `infra/terraform` -> pass
  - `python scripts/graphify.py --dry-run` -> would change 0 files

## Mental Model
- API keys are now looked up by SHA-256 digest in DynamoDB using PK `api_key_hash`; raw bearer tokens are not used as keys or returned.
- Literal `Bearer dev` only bypasses DynamoDB when `API_ENV=development` and `API_DEV_BYPASS=true`; ECS sets `API_DEV_BYPASS=false`.
- Terraform HTTPS listener now requires `var.acm_certificate_arn`; self-signed TLS resources/provider usage were removed.
- Terraform has partial S3 backend config plus `backend.example.hcl`; actual state migration still requires the real bucket/lock table and `terraform init -migrate-state`.
- CloudWatch alarms now cover ALB 5xx, ALB target response time, ingestion/embedding DLQ depth, and ingestion/embedding queue message age.

## Next Action
User can apply/migrate infra in AWS: provision state bucket + lock table, run Terraform init/apply with `acm_certificate_arn`, rotate any exposed API keys, and deploy/push updated Lambda/container artifacts.

## Do Not
- Do not edit .ai/MEMORY.md.
- Do not revert unrelated untracked docs under `docs/superpowers/`; they pre-existed this fix pass.
- Do not reintroduce DynamoDB raw-key storage, self-signed ALB certs, unconditional dev bypass, DynamoDB scans, or premature `embedded` registry updates.
