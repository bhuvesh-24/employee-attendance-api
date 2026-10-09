# Employee Attendance & Analytics API

FastAPI + MongoDB implementation of the HROne engineering assignment.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MONGO_URI="mongodb://localhost:27017"   # or Atlas URI
export MONGO_DB="attendance_db"
python sample_seed.py          # optional – loads the nine sample docs
uvicorn app.main:app --port 8000
```

`GET /health` returns 200 once Mongo answers a ping.

## What is implemented

- All contract endpoints (employees, punch-in/out, regularize, list, four analytics, admin explain)
- Business rules R1–R10
- Indexes created idempotently at startup
- Concurrent-safe punch-in / regularize via unique indexes and atomic updates
- REVIEW.md and DECISIONS.md filled in

No `.env`, secrets or Dockerfile are committed.
