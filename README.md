# Employee Attendance & Analytics API

FastAPI + MongoDB implementation of the HROne engineering assignment.

## Run

### Linux / macOS

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export MONGO_URI="mongodb://localhost:27017"   # or Atlas URI
export MONGO_DB="attendance_db"
uvicorn app.main:app --port 8000 --reload
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate
pip install -r requirements.txt
$env:MONGO_URI = "mongodb://localhost:27017"   # or Atlas URI
$env:MONGO_DB = "attendance_db"
uvicorn app.main:app --port 8000 --reload
```

`GET /health` returns 200 once Mongo answers a ping. Interactive docs: http://127.0.0.1:8000/docs

## What is implemented

- All contract endpoints (employees, punch-in/out, regularize, list, four analytics, admin explain)
- Business rules R1–R10
- Indexes created idempotently at startup
- Concurrent-safe punch-in / regularize via unique indexes and atomic updates
- REVIEW.md and DECISIONS.md filled in

No `.env`, secrets or Dockerfile are committed.

## Known limitations / notes for reviewers

- All required endpoints and docs (REVIEW.md, DECISIONS.md) are present.
- Department trend fills missing calendar days in Python rather than a pure MongoDB `$densify` aggregation pipeline (response shape still matches the contract: one row per day).
- `/admin/explain` uses simplified pipelines for some endpoints; the real list and analytics paths use the indexes created at startup.
- Not load-tested locally against a ~100k-record dataset (the grader dataset).
- `sample_seed.py` / sample JSON are not included in this repo; seed your own data or use the kit samples if needed.
