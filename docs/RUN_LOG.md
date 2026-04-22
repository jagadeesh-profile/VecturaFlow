# Run Log

## Setup
- `.venv\Scripts\Activate.ps1` - activate the Windows virtual environment in PowerShell.
- `python -m pip install -r requirements.txt` - install the project dependencies.
- `python -m pip install --only-binary=:all: pydantic-settings==2.13.1 boto3==1.34.102 openai==1.30.1 pinecone-client==3.2.2 python-dotenv==1.0.1` - install the minimum runtime deps from wheels only.
- `python -m pip install --only-binary=:all: httpx==0.27.0` - pin the OpenAI-compatible httpx version that resolved the client mismatch.

## Health Check Sequence
- `make preflight` - validate OpenAI credentials.
- `python scripts/preflight.py` - Windows equivalent for the OpenAI preflight check.
- `make pinecone-stats` - inspect Pinecone stats and sample stored vectors.
- `python scripts/verify_pinecone.py` - Windows equivalent for the Pinecone check.
- `make verify` - run the default RAG verification questions.
- `python scripts/verify_end_to_end.py` - Windows equivalent for the RAG verification.
- `make check-all` - run preflight, Pinecone stats, and verification in order.
- `python scripts/preflight.py; python scripts/verify_pinecone.py; python scripts/verify_end_to_end.py` - Windows equivalent for `make check-all`.

## Queue Triage
- `python scripts/triage_queue.py` - dry-run ingestion queue triage.
- `python scripts/triage_queue.py --apply` - apply drain/reprocess/DLQ decisions.

## Calibration
- `python scripts/verify_end_to_end.py --calibrate` - run the default questions with debug output and threshold recommendations.
- `python scripts/verify_end_to_end.py --debug` - inspect one question with full retrieval and prompt details.

## Windows equivalents
- `make` is unavailable in the Windows dev shell, so use the direct `python scripts/...` commands for preflight, Pinecone stats, verification, queue triage, and calibration.
- `python scripts/preflight.py` - replacement for `make preflight`.
- `python scripts/verify_pinecone.py` - replacement for `make pinecone-stats`.
- `python scripts/verify_end_to_end.py` - replacement for `make verify`.
- `python scripts/preflight.py; python scripts/verify_pinecone.py; python scripts/verify_end_to_end.py` - replacement for `make check-all`.
- `python scripts/triage_queue.py` - replacement for `make triage`.
- `python scripts/triage_queue.py --apply` - replacement for `make triage-apply`.
- `python scripts/verify_end_to_end.py --calibrate` - replacement for calibration on Windows.

## Known issues resolved during build
- NumPy source-build failure - fixed by installing minimal deps with `--only-binary`.
- httpx/openai version mismatch - fixed by pinning `httpx==0.27.0`.
- Retrieval threshold too strict (`0.70`) - replaced with calibrated `RETRIEVAL_THRESHOLD`.