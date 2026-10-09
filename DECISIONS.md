# DECISIONS.md

1. **Indexes.** Unique on `employees.emp_code` (duplicate create → 409) and on `attendance_logs (emp_code, date)` (punch-in race). Compound `(date DESC, emp_code ASC)` and `(emp_code, date DESC)` for list/attendance filters. `(department, joined_on)` and plain `joined_on` for headcount. Rejected a single huge compound covering every analytics filter—too wide and unused by most queries.

2. **Punch-in race.** Two identical requests hit the unique index. One `insert_one` succeeds (201); the other raises `DuplicateKeyError` and becomes 409. No application-level lock needed.

3. **Ties.** Standard competition ranking: equal `total_late_minutes` share the same rank and the next rank is skipped (1, 2, 2, 4). `limit` is applied after ranking, so a tie that straddles the cutoff still appears with its true rank.

4. **Headcount.** Department summary starts from the `employees` collection filtered by `joined_on <= last day of month` (and optional department). Employees with zero logs still appear because they are never joined away; only their log-derived counters stay zero.

5. **100× data.** Pre-aggregate monthly roll-ups into a side collection (or materialized view) refreshed by a change-stream worker, and keep only the hot indexes. The current pure-aggregation paths would become the fallback for ad-hoc ranges.
