# VecturaFlow — Operations Runbook

What to do when something breaks, something scales, or you need to push a change safely.

---

## 1. Deploy a new version

Normal path — a merge to `main` triggers `.github/workflows/deploy.yml`:

1. OIDC-federate into AWS (no long-lived keys).
2. `docker buildx build` with the commit SHA as the tag; push to ECR.
3. Render a new task definition with the new image URI.
4. `ecs deploy-task-definition --wait-for-service-stability`.
5. `curl -f https://<alb>/health` 30 times at 5-second intervals; fail the job on any 5xx.

You can watch the deployment from the CLI:

```bash
aws ecs describe-services \
  --cluster vecturaflow-prod \
  --services vecturaflow-api \
  --query 'services[0].deployments[].{status:status,rollout:rolloutState,desired:desiredCount,running:runningCount}' \
  --output table
```

---

## 2. Rollback

The ECS deployment circuit-breaker will roll back automatically on health-check failure.
If you need to roll back a good deployment because of an application-level regression:

```bash
# list task definitions — pick the previous green one
aws ecs list-task-definitions --family-prefix vecturaflow-api --sort DESC --max-items 5

# update the service to point at it
aws ecs update-service \
  --cluster vecturaflow-prod \
  --service vecturaflow-api \
  --task-definition vecturaflow-api:<PREVIOUS_N> \
  --force-new-deployment
```

Watch the same `describe-services` command above until rollout is `COMPLETED`.

---

## 3. Replay a DLQ

Both ingestion and embedding queues have DLQs with a CloudWatch alarm on
`ApproximateNumberOfMessagesVisible > 0`. To inspect and replay:

```bash
# peek (doesn't delete)
aws sqs receive-message \
  --queue-url $INGESTION_DLQ_URL \
  --max-number-of-messages 10 \
  --visibility-timeout 0

# replay one by one — usually after fixing whatever the parser / embedder couldn't handle
aws sqs start-message-move-task \
  --source-arn $INGESTION_DLQ_ARN \
  --destination-arn $INGESTION_QUEUE_ARN \
  --max-number-of-messages-per-second 5
```

If the DLQ contains truly-bad messages (corrupt PDFs, for example), archive them to S3 for
post-mortem and purge:

```bash
aws sqs purge-queue --queue-url $INGESTION_DLQ_URL
```

---

## 4. Rate limit incidents

Current limiter is **per-process, in-memory**. Each Fargate task has its own bucket, so the
effective limit is `RATE_LIMIT_PER_MINUTE × N_tasks`. If a customer is hitting 429s but usage
looks reasonable for their plan:

1. Check `vecturaflow_http_requests_total{status="429"}` in Grafana.
2. If a single `key_id` dominates, that key is being hammered — probably a runaway client
   loop; page the customer.
3. If 429s are spread across many keys, the service is under real load — bump the min task
   count on the Fargate service or raise `RATE_LIMIT_PER_MINUTE` via Terraform.
4. **Permanent fix** (tracked): swap `api/rate_limit.py` for an ElastiCache-backed Lua-script
   implementation so the limit is exact across workers.

---

## 5. Pinecone / OpenAI outages

Both are third-party. VecturaFlow's handling:

| Outage       | Caller sees                                                                          |
|--------------|--------------------------------------------------------------------------------------|
| OpenAI down  | 503 with `"message": "RAG pipeline is temporarily unavailable. Please retry."`       |
| Pinecone down | `confidence=no_context`, polite "don't have that info" reply. No retry loop for the client. |
| Redis down   | Logged, retriever falls through to live Pinecone. No caller-visible effect.          |

In all three cases, `vecturaflow_rag_queries_total{confidence="error"}` ticks up. Set a
Grafana alert on a sustained rate > 1/min.

---

## 6. Cost controls

Ordered by impact:

1. **OpenAI embeddings** — the retriever caches query results in Redis for 5 minutes. Keep an
   eye on `vecturaflow_retriever_cache_total{outcome="hit"}` / `miss` ratio. Below 40% hit
   rate, increase `_CACHE_TTL` in `api/retriever.py` before scaling Redis.
2. **OpenAI generation** — temperature is locked at 0 and `max_tokens` defaults to `None`,
   which lets GPT-4o mini decide. Cap it per-tier by setting `max_tokens` in the client, or
   clamp it server-side in `api/main.py` if a runaway client is burning budget.
3. **Pinecone reads** — MMR over-fetches 20 candidates; that's the full query cost whether
   the user asked for `top_k=5` or `top_k=10`. For a lighter-weight read path, call
   `retrieve(..., use_mmr=False)` from internal callers.
4. **ECS Fargate** — `vecturaflow-api` autoscales 2–8 tasks on 60% CPU. Check CloudWatch
   Container Insights for headroom; drop the min to 1 in dev/staging.

---

## 7. Adding an API key

```python
import boto3
table = boto3.resource("dynamodb").Table("vecturaflow-keys")
table.put_item(Item={
    "api_key": "vf_" + "<random 32 hex>",
    "key_id":  "acme-prod-001",
    "owner":   "ops@acme.example",
    "revoked": False,
})
```

Revoke by setting `revoked=True`; the next request will 401 with code `revoked_key`.

> **Known limitation:** keys are stored as plaintext PKs. A hardening ticket replaces the
> PK with `sha256(api_key)` and looks up via a hash of the presented bearer token. Until
> then, access to the keys table is IAM-restricted to the API task role and the operator.

---

## 8. Chaos drills we've rehearsed

| Drill                                     | Outcome                                                 |
|-------------------------------------------|---------------------------------------------------------|
| Kill a Fargate task mid-flight            | ALB evicts, autoscaler replaces, p99 blip ~4s.          |
| Pause Redis                               | Retriever logs `retriever.redis_unavailable`, latency +80 ms, no errors. |
| Block OpenAI egress                       | 503s to caller, no task crashes, metrics tick correctly.|
| Flood 200 rps with a single key           | 429s start at the advertised limit, rest of the keyspace unaffected. |
| Dump a malformed PDF to the bucket        | Lambda emits `parse_failed` status, partial-batch response; other docs still process. |

---

## 9. Local reproducers

To reproduce a production issue locally without touching AWS:

```bash
docker-compose up --build   # LocalStack + Redis + API
# reseed data
python scripts/demo.py      # walks the full ingest → query cycle against LocalStack
```

Tail logs:

```bash
docker-compose logs -f api
```

Hit the API with the dev key:

```bash
curl -sX POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"What changed in Q3?"}]}' | jq
```
