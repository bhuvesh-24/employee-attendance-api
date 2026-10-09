# REVIEW.md

List every defect found in the starter `app/main.py` (helpers and endpoints).

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health` | Does not ping MongoDB; always returns 200 | Grader expects 503 when Mongo is down | Added `client.admin.command("ping")` and 503 on failure |
| 2 | `create_employee` | No unique index; concurrent duplicate creates can both succeed | Two parallel POSTs with same emp_code both return 201 | Unique index on `emp_code` + catch `DuplicateKeyError` → 409 |
| 3 | `create_employee` | Missing check that `shift_start != shift_end` | OpenAPI requires it | Explicit 422 validation |
| 4 | `create_employee` | `created_at` is naive `datetime.now()`; response never converts to epoch ms | Contract requires epoch milliseconds | Store UTC aware, serialize with `to_epoch_ms` |
| 5 | `list_employees` | `total = count_documents({})` ignores filter; `skip = page * page_size` (off-by-one); no sort | Wrong page totals and empty first page | Filtered count, `(page-1)*page_size`, sort by `emp_code` ASC |
| 6 | `compute_late_minutes` | Replaces hour/minute on the punch datetime itself (wrong timezone / overnight); uses `> 10` but floor minutes from start is correct only by chance; no second truncation | Overnight shift or non-UTC punch yields wrong late minutes | Build shift-start from attendance date in IST, truncate seconds, apply R2 strictly |
| 7 | `compute_work_hours` | Uses Python `round` (banker's rounding) | Values ending in .xx5 can be off | Half-up via `Decimal` |
| 8 | `compute_overtime` | Builds end from `date_str` without overnight handling; no ≥30 gate | Overnight OT wrong; OT of 20 min returned as 20 | `shift_end_dt` adds a day for overnight; return 0 when < 30 |
| 9 | `punch_in` | No 404 when employee missing; naive datetime; date = UTC date not IST/R1; returns Mongo `_id` as `id`; no epoch conversion; race on concurrent insert | 500 / wrong date / contract violation / double records | Lookup → 404, R1 date helper, unique index, serialize to contract shape |
| 10 | `list_attendance` | Loads entire result set into memory then slices; adds `id`; no epoch conversion; page_size unbounded | OOM on 100k docs; wrong response shape | Mongo skip/limit + indexes; proper serialization; page_size ≤ 100 |
| 11 | Missing endpoints | punch-out, regularize, all analytics, explain | 404 on every required path | Implemented all remaining contract endpoints |
| 12 | No indexes at startup | Analytics and list endpoints scan collections | Explain shows COLLSCAN | `ensure_indexes()` on startup (idempotent) |

Also looked at and decided **not** defects:
- Default shift times `"09:30"`/`"18:30"` are reasonable and match sample data.
- Using PyMongo (sync) is allowed; Motor is optional.
