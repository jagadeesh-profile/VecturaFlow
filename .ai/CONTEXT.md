# Live Context (overwritten at every session end)

## Current Task
Execute all 7 production hardening rollout steps

## State
- Phase: deployed and verified; rollout code/docs changes are pending commit/push.
- Pushed prior hardening commit `b8563a0` to `origin/main`.
- Built/pushed API image `383175541991.dkr.ecr.us-east-1.amazonaws.com/vecturaflow-prod-api:b8563a0`.
- Fixed Lambda image packaging/build issues, then pushed Lambda image
  `383175541991.dkr.ecr.us-east-1.amazonaws.com/vecturaflow-prod-ingest:b8563a0`
  as a Lambda-compatible Docker v2 single manifest.
- Terraform remote backend is active in S3; final production `terraform plan` returned
  `No changes`.
- ECS service `vecturaflow-prod-api` is stable on task definition revision 2 with
  `KEYS_TABLE=vecturaflow-prod-keys-v2`.
- Three ingestion Lambdas are Active and all SQS event-source mappings are Enabled.
- Migrated 2 legacy raw API keys into `vecturaflow-prod-keys-v2` as SHA-256 hashes,
  verified zero raw `api_key` attributes, and deleted legacy table
  `vecturaflow-prod-keys`.
- Route53 hosted zone `chatslm.com.` has the ACM validation CNAME and
  `vecturaflow.chatslm.com` ALB alias. Public DNS is still delegated to GoDaddy
  (`ns45.domaincontrol.com`, `ns46.domaincontrol.com`), so the Amazon-issued cert
  `arn:aws:acm:us-east-1:383175541991:certificate/b22fb3b4-9807-468b-bb35-8ea54f6f499e`
  remains `PENDING_VALIDATION`.
- Route53 nameservers to delegate/copy from: `ns-229.awsdns-28.com`,
  `ns-1700.awsdns-20.co.uk`, `ns-1094.awsdns-08.org`, `ns-932.awsdns-52.net`.
- Smoke tests:
  - `https://vecturaflow-prod-alb-635509785.us-east-1.elb.amazonaws.com/health`
    returned 200.
  - `/v1/models` returned 200 with a migrated production key before old-table deletion.
  - `/v1/models` returned 200 with a temporary v2-only hashed smoke key after old-table
    deletion; the temporary key was deleted.
  - `Bearer dev` returned 401 in production.
- Verification:
  - Full pytest: 126 passed, 82.85% coverage, 1 Starlette/python_multipart warning.
  - Scoped ruff: pass.
  - `terraform fmt -check -recursive infra/terraform`: pass.
  - `terraform validate`: pass.
  - `python scripts/graphify.py --dry-run`: would change 0 files.
  - Broad `ruff check ... scripts` still reports unrelated pre-existing script lint debt.

## Mental Model
- Production no longer stores raw API keys in DynamoDB; auth uses only
  `api_key_hash` in `vecturaflow-prod-keys-v2`.
- The old raw-key table was deliberately deleted only after ECS was stable on v2 and
  v2-only auth smoke passed.
- Lambda images must be built with `docker buildx build --platform linux/amd64
  --provenance=false --sbom=false ... --push`; otherwise Lambda rejects the default
  OCI index/attestation manifest.
- Lambda environment blocks must not set reserved region keys such as
  `AWS_DEFAULT_REGION`; handlers use Lambda-provided `AWS_REGION`.
- SQS Lambda filters live under `body`; nested object filters use leaf arrays, not
  an object wrapped in a list.

## Next Action
Commit and push the rollout fix commit. Then fix public DNS at GoDaddy: either change
`chatslm.com` nameservers to the Route53 nameservers above or copy the ACM validation
CNAME into GoDaddy DNS. After ACM becomes `ISSUED`, run Terraform with the Amazon-issued
certificate ARN to replace the current imported ALB certificate.

## Do Not
- Do not edit `.ai/MEMORY.md`.
- Do not restore the deleted legacy raw-key table.
- Do not reintroduce raw-key storage, self-signed ALB TLS, unconditional dev bypass,
  Lambda `AWS_DEFAULT_REGION` env vars, or Lambda OCI image indexes with attestations.
- Do not revert the pre-existing untracked `docs/superpowers/...` files.
