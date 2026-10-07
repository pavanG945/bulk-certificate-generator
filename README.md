# Bulk Certificate Generator

A FastAPI backend that accepts one request containing many recipients, generates a PDF certificate for each from a single predefined template, tracks progress, and lets the client download the results.

**Stack:** Python 3.10+, FastAPI, SQLAlchemy (SQLite by default; any relational DB via `DATABASE_URL`), fpdf2 for PDFs, pytest.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Interactive API docs: http://localhost:8000/docs. Tables are created automatically on startup.

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | SQLAlchemy database URL |
| `CERTIFICATE_DIR` | `./data/certificates` | Where PDFs are stored |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Request size cap (413 above this) |

## Tests

```bash
pytest
```

Tests use an in-memory database and a temp directory, so nothing touches `./data`.

## API

### Submit a job: `POST /api/jobs` → `202 Accepted`

```bash
curl -X POST http://localhost:8000/api/jobs -H "Content-Type: application/json" -d '{
  "title": "Python Bootcamp",
  "issuer": "Acme Academy",
  "issue_date": "2026-09-30",
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "José Núñez"},
    {"name": ""}
  ]
}'
```

`issuer` and `issue_date` are optional (defaults: "Certificate Authority", today). Response:

```json
{"id": "59eeefa8...", "status": "PENDING", "total": 3, "status_url": "http://localhost:8000/api/jobs/59eeefa8..."}
```

### Check progress: `GET /api/jobs/{id}`

```json
{
  "id": "59eeefa8...", "status": "COMPLETED_WITH_ERRORS", "title": "Python Bootcamp",
  "total": 3, "succeeded": 2, "failed": 1, "pending": 0, "progress_percent": 100.0,
  "created_at": "...", "completed_at": "...",
  "download_url": "http://localhost:8000/api/jobs/59eeefa8.../download"
}
```

Job status: `PENDING` → `PROCESSING` → `COMPLETED` (all succeeded) / `COMPLETED_WITH_ERRORS` (some failed) / `FAILED` (none succeeded).

### Per-recipient results: `GET /api/jobs/{id}/certificates[?status_filter=FAILED]`

```json
[
  {"id": "ab12...", "position": 0, "recipient_name": "Asha Rao", "recipient_email": "asha@example.com",
   "status": "SUCCESS", "error": null, "download_url": "http://localhost:8000/api/certificates/ab12.../download"},
  {"id": "cd34...", "position": 2, "recipient_name": "", "recipient_email": null,
   "status": "FAILED", "error": "Invalid recipient: name: String should have at least 1 character", "download_url": null}
]
```

`position` is the index in the submitted list, so failures can be mapped back to the input.

### Retrieve certificates

- One PDF: `GET /api/certificates/{certificate_id}/download`
- All successful PDFs as a ZIP: `GET /api/jobs/{id}/download` (409 while the job is still running)

### Errors

`422` malformed request (empty list, missing title, bad date) · `413` too many recipients · `404` unknown job/certificate · `409` not ready yet.

## Design decisions

**Background processing, not synchronous.** `POST /api/jobs` validates, stores the job and returns `202` immediately; generation runs in a FastAPI `BackgroundTask`. A synchronous design would hold the HTTP connection open for the whole batch, risking client/proxy timeouts for large lists, and gives no meaningful "progress". The client polls `GET /api/jobs/{id}`. The task commits after each certificate, so progress is live.

**Why not Celery/RQ?** For this scope a queue adds a broker and worker process to run and explain. `BackgroundTasks` keeps setup to one command. The trade-off is durability: if the server restarts mid-job, that job stays `PROCESSING`. In production I would move `process_job` (already a plain function taking a job id) onto Celery/RQ with Postgres; the router would change by one line.

**Two layers of validation.**
1. *Request-level* (Pydantic): the request must have a title, a valid date and a non-empty recipient list. Otherwise `422`.
2. *Recipient-level*: each recipient is validated on its own. An invalid one (blank name, bad email, wrong shape) is stored as `FAILED` with the reason, and the rest of the job still runs. Rejecting a 500-recipient request because of one typo would contradict the failure-isolation requirement, and storing the failed rows means the client sees every submitted recipient in the result.

**Failure isolation.** Each certificate is generated in its own `try/except` and commit. An outer guard marks leftover `PENDING` rows `FAILED` on unexpected errors (e.g. disk full), so a job is never stuck in `PROCESSING` after the task exits.

**Storage.** PDFs go on disk (`CERTIFICATE_DIR/<job_id>/<certificate_id>.pdf`); the database stores metadata and the path. Swapping to S3 would only touch the write in `process_job` and the download endpoints.

**Template.** One hard-coded landscape A4 layout in `app/services/certificate_generator.py` (fpdf2). The generator is a pure function, `(name, title, issuer, date) -> bytes`, which makes it easy to test and replace.

**Known limitations**
- The built-in PDF font covers Latin-1 only. Names in other scripts (e.g. CJK, Devanagari) fail for that certificate with a clear error. The fix is to bundle a Unicode TTF (e.g. Noto Sans) via `pdf.add_font`.
- The ZIP is built in memory; for very large jobs it should be streamed.
- No authentication or per-client job ownership; job IDs are unguessable UUIDs but not secret-grade access control.
- No schema migrations (tables created with `create_all`); I'd add Alembic for a real deployment.

## Project layout

```
app/
  main.py                      app + lifespan (creates tables)
  config.py, database.py       settings, engine/session
  models.py                    Job, Certificate (one row per submitted recipient)
  schemas.py                   request/response models, recipient validation
  routers/jobs.py              HTTP endpoints
  services/jobs.py             create_job (validation) and process_job (background work)
  services/certificate_generator.py   PDF template
tests/                         pytest suite (16 tests)
```
