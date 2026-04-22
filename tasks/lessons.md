# VecturaFlow — Lessons

- If S3 uploads do not trigger chunking, verify the deployed Lambda image and event-source wiring before changing the parser or chunker.
- S3 notifications delivered through SQS can arrive as raw S3 event bodies; the parser must accept that shape if the queue is shared.
- A local code fix does not affect AWS until the Lambda image is rebuilt and redeployed.
- Prefer `AWS_REGION` with `AWS_DEFAULT_REGION` fallback in AWS-facing code; Lambda and local shells do not always expose the same region env names.