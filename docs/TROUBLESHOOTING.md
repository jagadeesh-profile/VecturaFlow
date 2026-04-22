# Troubleshooting

See the detailed failure matrix and fixes in [docs/HEALTH_CHECK.md](docs/HEALTH_CHECK.md).

## Verify failures

| Symptom | Likely cause | Fix |
|---|---|---|
| All scores between 0.4–0.6 but clearly relevant | Threshold calibrated for wrong embedding model | Run `python scripts/verify_end_to_end.py --calibrate` and update `RETRIEVAL_THRESHOLD` in `.env`. |