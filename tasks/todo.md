# VecturaFlow — Outstanding Tasks

## CI / Security

### Dependency CVE remediation (tracked in CI as non-blocking warnings)

Trivy surfaces the following CRITICAL/HIGH CVEs in the container image
(`ci.yml` → `docker-build` job → `Trivy vulnerability scan` step,
`continue-on-error: true`). They are not gating CI today because the
fixes require a cross-cutting langchain version bump.

| Package                     | CVE              | Severity | Fixed in         |
|-----------------------------|------------------|----------|------------------|
| `langchain-core`            | CVE-2025-68664   | CRITICAL | 1.2.5, 0.3.81    |
| `langchain-core`            | CVE-2025-65106   | HIGH     | 1.0.7, 0.3.80    |
| `langchain`                 | CVE-2026-34070   | HIGH     | 1.2.22           |
| `langchain-community`       | (various)        | HIGH     | 0.3+             |
| `langchain-text-splitters`  | CVE-2025-6985    | HIGH     | 0.3.9            |
| `starlette` (via fastapi)   | CVE-2024-47874   | HIGH     | 0.40.0 (fastapi ≥ 0.115) |
| `wheel` (build tooling)     | CVE-2026-24049   | HIGH     | 0.46.2           |
| `jaraco.context`            | CVE-2026-23949   | HIGH     | 6.1.0            |
| `libssl3` (debian)          | CVE-2026-28390   | HIGH     | 3.0.19-1~deb12u2 |

**Remediation plan (single sprint):**

1. Bump `fastapi==0.111.0` → `fastapi==0.115.6` (pulls starlette ≥ 0.40.0)
2. Bump the langchain stack as a set:
   - `langchain==0.3.27`
   - `langchain-openai==0.2.14`
   - `langchain-community==0.3.27`
   - `langchain-text-splitters==0.3.9`
   - `langgraph==0.2.76`
3. Run full test suite + `poc/live_api_test.py` against dev ALB
4. Re-pin `requirements.runtime.txt` identically
5. Flip `exit-code: "1"` and remove `continue-on-error` on the Trivy step
6. Rebase ECS task definition on the new image

**Risk:** langchain 0.2 → 0.3 has import-path changes and some deprecated
APIs (`ChatPromptTemplate.from_messages` behavior, `RunnableSerializable`
changes). The LangGraph StateGraph in `api/agent.py` needs audit.

### Other flagged issues (from earlier review)

- [ ] Replace self-signed ALB cert with ACM-managed cert
- [ ] Hash API keys in DynamoDB (currently stored plaintext in `keys` table)
- [ ] Migrate off root AWS user → dedicated IAM user with MFA
- [ ] Move Terraform state from local `.tfstate` to S3+DynamoDB backend
- [ ] Deploy missing ingestion Lambdas (Path B from session 2026-04-21)
   - Parser now accepts raw S3 notifications, but the updated Lambda image still has to be pushed and the AWS function updated.
- [ ] Rotate the two API keys that were displayed in chat history
- [ ] Add CloudWatch alarms for: 5xx rate, p95 latency, DLQ depth, SQS age
- [ ] Remove double-logging in `api/observability.py`
- [ ] Narrow ECS task egress SG from `0.0.0.0/0` → VPC endpoints only
- [ ] Drop unused `dynamodb:Scan` from task role (we only Query via GSI)
- [ ] Harden dev bypass: require env `API_ENV=development` *and* explicit
      `API_DEV_BYPASS=true` before accepting literal key `"dev"`
