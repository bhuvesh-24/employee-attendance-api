"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience; real env vars win)
"""
from __future__ import annotations

import os
from calendar import monthrange
from datetime import datetime, timedelta, timezone, date, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

# Prefer real environment variables over .env
MONGO_URI = os.environ.get("MONGO_URI") or "mongodb://localhost:27017"
MONGO_DB = os.environ.get("MONGO_DB") or "attendance_db"

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[MONGO_DB]

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def ensure_indexes() -> None:
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uniq_emp_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="dept_emp")
    db.employees.create_index([("joined_on", ASCENDING)], name="joined_on")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="dept_joined")

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uniq_emp_date"
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)], name="date_emp"
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", DESCENDING)], name="emp_date"
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)], name="status_date"
    )
    db.attendance_logs.create_index(
        [("date", ASCENDING), ("emp_code", ASCENDING)], name="date_asc_emp"
    )


@app.on_event("startup")
def on_startup() -> None:
    ensure_indexes()


def half_up_2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def half_up_4(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def truncate_to_seconds(dt: datetime) -> datetime:
    return dt.replace(microsecond=0)


def to_epoch_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    if not isinstance(ms, int) or ms < 100_000_000_000:
        raise HTTPException(status_code=422, detail="must be epoch milliseconds")
    dt = datetime.fromtimestamp(ms / 1000.0, tz=UTC)
    return truncate_to_seconds(dt)


def is_overnight(shift_start: str, shift_end: str) -> bool:
    return shift_end <= shift_start


def parse_hhmm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def attendance_date_for_punch(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    local = punch_in.astimezone(IST)
    d = local.date()
    if is_overnight(shift_start, shift_end):
        end_t = parse_hhmm(shift_end)
        if local.time() < end_t:
            d = d - timedelta(days=1)
    return d.isoformat()


def shift_start_dt(att_date: str, shift_start: str) -> datetime:
    d = date.fromisoformat(att_date)
    t = parse_hhmm(shift_start)
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=IST).astimezone(UTC)


def shift_end_dt(att_date: str, shift_start: str, shift_end: str) -> datetime:
    d = date.fromisoformat(att_date)
    t = parse_hhmm(shift_end)
    end = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=IST)
    if is_overnight(shift_start, shift_end):
        end = end + timedelta(days=1)
    return end.astimezone(UTC)


def compute_late_minutes(punch_in: datetime, att_date: str, shift_start: str) -> int:
    start = shift_start_dt(att_date, shift_start)
    delta_sec = (punch_in - start).total_seconds()
    if delta_sec <= 10 * 60:
        return 0
    return int(delta_sec // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    secs = (punch_out - punch_in).total_seconds()
    return half_up_2(secs / 3600.0)


def compute_overtime(punch_out: datetime, att_date: str, shift_start: str, shift_end: str) -> int:
    end = shift_end_dt(att_date, shift_start, shift_end)
    mins = int((punch_out - end).total_seconds() // 60)
    return mins if mins >= 30 else 0


def compute_half_day(work_hours: Optional[float]) -> bool:
    if work_hours is None:
        return False
    return work_hours < 4.50


def serialize_employee(doc: dict) -> dict:
    return {
        "emp_code": doc["emp_code"],
        "name": doc["name"],
        "email": doc["email"],
        "department": doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end": doc["shift_end"],
        "joined_on": doc["joined_on"],
        "created_at": to_epoch_ms(doc.get("created_at")),
    }


def serialize_attendance(doc: dict) -> dict:
    history = []
    for h in doc.get("history") or []:
        changes = {}
        for k, v in (h.get("changes") or {}).items():
            if k in ("punch_in", "punch_out"):
                changes[k] = {
                    "from": to_epoch_ms(v.get("from")),
                    "to": to_epoch_ms(v.get("to")),
                }
            else:
                changes[k] = {"from": v.get("from"), "to": v.get("to")}
        history.append({
            "at": to_epoch_ms(h.get("at")),
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": changes,
        })
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": to_epoch_ms(doc.get("punch_in")),
        "punch_out": to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": bool(doc.get("half_day", False)),
        "history": history,
    }


def working_days_in_range(start: date, end: date) -> int:
    if start > end:
        return 0
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


def month_bounds(month: str) -> tuple[str, str, date, date]:
    y, m = map(int, month.split("-"))
    first = date(y, m, 1)
    last = date(y, m, monthrange(y, m)[1])
    return first.isoformat(), last.isoformat(), first, last


class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_hhmm(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
            raise ValueError("must be HH:MM")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("invalid time")
        return f"{h:02d}:{m:02d}"

    @field_validator("joined_on")
    @classmethod
    def valid_date(cls, v: str) -> str:
        date.fromisoformat(v)
        return v


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        if v not in ("PRESENT", "WFH", "ON_DUTY"):
            raise ValueError("status must be PRESENT, WFH or ON_DUTY")
        return v


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    by: str
    reason: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"):
            raise ValueError("invalid status")
        return v


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        raise HTTPException(status_code=422, detail="shift_start must differ from shift_end")
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q: dict[str, Any] = {}
    if department is not None:
        q["department"] = department
    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        db.employees.find(q, {"_id": 0})
        .sort([("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )
    items = [serialize_employee(d) for d in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = truncate_to_seconds(datetime.now(UTC))

    att_date = attendance_date_for_punch(ts, emp["shift_start"], emp["shift_end"])
    late = compute_late_minutes(ts, att_date, emp["shift_start"])

    doc = {
        "emp_code": body.emp_code,
        "date": att_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="already punched in for this date")
    return serialize_attendance(doc)


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    if body.punched_at is not None:
        ts = from_epoch_ms(body.punched_at)
    else:
        ts = truncate_to_seconds(datetime.now(UTC))

    open_rec = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", DESCENDING)],
    )
    if not open_rec:
        raise HTTPException(status_code=404, detail="no open punch-in found")

    pin = open_rec["punch_in"]
    if pin.tzinfo is None:
        pin = pin.replace(tzinfo=UTC)
    if ts <= pin:
        raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
    if (ts - pin).total_seconds() > 24 * 3600:
        raise HTTPException(status_code=422, detail="punch_out more than 24h after punch_in")

    att_date = open_rec["date"]
    wh = compute_work_hours(pin, ts)
    ot = compute_overtime(ts, att_date, emp["shift_start"], emp["shift_end"])
    hd = compute_half_day(wh)

    updated = db.attendance_logs.find_one_and_update(
        {"_id": open_rec["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts,
                "work_hours": wh,
                "overtime_minutes": ot,
                "half_day": hd,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="already punched out")
    return serialize_attendance(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")

    q: dict[str, Any] = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    cursor = (
        db.attendance_logs.find(q)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )
    items = [serialize_attendance(d) for d in cursor]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{date}")
def regularize(
    emp_code: str,
    date: str = Path(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    body: RegularizeIn = ...,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not rec:
        raise HTTPException(status_code=404, detail="attendance record not found")

    new_status = body.status if body.status is not None else rec["status"]
    new_pin = from_epoch_ms(body.punch_in) if body.punch_in is not None else rec.get("punch_in")
    new_pout = from_epoch_ms(body.punch_out) if body.punch_out is not None else rec.get("punch_out")

    if new_status in ("ABSENT", "LEAVE"):
        new_pin = None
        new_pout = None

    if new_pin is not None and new_pout is not None:
        if new_pin.tzinfo is None:
            new_pin = new_pin.replace(tzinfo=UTC)
        if new_pout.tzinfo is None:
            new_pout = new_pout.replace(tzinfo=UTC)
        if new_pout <= new_pin:
            raise HTTPException(status_code=422, detail="punch_out must be after punch_in")
        if (new_pout - new_pin).total_seconds() > 24 * 3600:
            raise HTTPException(status_code=422, detail="punch_out more than 24h after punch_in")

    if new_pin is not None:
        late = compute_late_minutes(new_pin, date, emp["shift_start"])
    else:
        late = 0

    if new_pin is not None and new_pout is not None:
        wh = compute_work_hours(new_pin, new_pout)
        ot = compute_overtime(new_pout, date, emp["shift_start"], emp["shift_end"])
        hd = compute_half_day(wh)
    else:
        wh = None
        ot = 0
        hd = False

    changes: dict[str, Any] = {}
    old_status = rec["status"]
    old_pin = rec.get("punch_in")
    old_pout = rec.get("punch_out")
    old_wh = rec.get("work_hours")
    old_late = rec.get("late_minutes", 0)
    old_ot = rec.get("overtime_minutes", 0)
    old_hd = bool(rec.get("half_day", False))

    if new_status != old_status:
        changes["status"] = {"from": old_status, "to": new_status}
    if to_epoch_ms(new_pin) != to_epoch_ms(old_pin):
        changes["punch_in"] = {"from": old_pin, "to": new_pin}
    if to_epoch_ms(new_pout) != to_epoch_ms(old_pout):
        changes["punch_out"] = {"from": old_pout, "to": new_pout}
    if wh != old_wh:
        changes["work_hours"] = {"from": old_wh, "to": wh}
    if late != old_late:
        changes["late_minutes"] = {"from": old_late, "to": late}
    if ot != old_ot:
        changes["overtime_minutes"] = {"from": old_ot, "to": ot}
    if hd != old_hd:
        changes["half_day"] = {"from": old_hd, "to": hd}

    if not changes:
        raise HTTPException(status_code=422, detail="no changes")

    history_entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.by,
        "reason": body.reason,
        "changes": changes,
    }

    updated = db.attendance_logs.find_one_and_update(
        {"emp_code": emp_code, "date": date},
        {
            "$set": {
                "status": new_status,
                "punch_in": new_pin,
                "punch_out": new_pout,
                "work_hours": wh,
                "late_minutes": late,
                "overtime_minutes": ot,
                "half_day": hd,
            },
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )
    return serialize_attendance(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="employee not found")

    date_from, date_to, first, last = month_bounds(month)
    joined = date.fromisoformat(emp["joined_on"])
    start = max(first, joined)
    working_days = working_days_in_range(start, last)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": date_from, "$lte": date_to},
            }
        },
        {
            "$addFields": {
                "dow": {
                    "$dayOfWeek": {
                        "$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$dow", [2, 3, 4, 5, 6]]},
                                ]
                            },
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }
        },
    ]
    res = list(db.attendance_logs.aggregate(pipeline))
    if res:
        r = res[0]
        present_days = r["present_days"]
        leave_days = r["leave_days"]
        late_count = r["late_count"]
        total_late = r["total_late_minutes"]
        total_ot = r["total_overtime_minutes"]
    else:
        present_days = leave_days = late_count = total_late = total_ot = 0

    pct = None if working_days == 0 else half_up_2(present_days / working_days * 100)

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late,
        "total_overtime_minutes": total_ot,
        "attendance_pct": pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    date_from, date_to, first, last = month_bounds(month)
    end_str = last.isoformat()

    emp_match: dict[str, Any] = {"joined_on": {"$lte": end_str}}
    if department:
        emp_match["department"] = department

    pipeline = [
        {"$match": emp_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", date_from]},
                                    {"$lte": ["$date", date_to]},
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "dow": {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d",
                                    }
                                }
                            }
                        }
                    },
                ],
                "as": "logs",
            }
        },
        {
            "$project": {
                "department": 1,
                "present_days": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {
                                "$cond": [
                                    {
                                        "$and": [
                                            {
                                                "$in": [
                                                    "$$l.status",
                                                    ["PRESENT", "WFH", "ON_DUTY"],
                                                ]
                                            },
                                            {"$in": ["$$l.dow", [2, 3, 4, 5, 6]]},
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {"$eq": ["$$l.half_day", True]},
                                            0.5,
                                            1,
                                        ]
                                    },
                                    0,
                                ]
                            },
                        }
                    }
                },
                "work_hours_list": {
                    "$map": {
                        "input": {
                            "$filter": {
                                "input": "$logs",
                                "as": "l",
                                "cond": {"$ne": ["$$l.work_hours", None]},
                            }
                        },
                        "as": "l",
                        "in": "$$l.work_hours",
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "l",
                            "cond": {"$gt": ["$$l.late_minutes", 0]},
                        }
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$map": {
                            "input": "$logs",
                            "as": "l",
                            "in": {"$ifNull": ["$$l.late_minutes", 0]},
                        }
                    }
                },
                "leave_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "l",
                            "cond": {"$eq": ["$$l.status", "LEAVE"]},
                        }
                    }
                },
                "on_duty_count": {
                    "$size": {
                        "$filter": {
                            "input": "$logs",
                            "as": "l",
                            "cond": {"$eq": ["$$l.status", "ON_DUTY"]},
                        }
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$present_days"},
                "all_work_hours": {"$push": "$work_hours_list"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {
            "$project": {
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
                "avg_work_hours": {
                    "$let": {
                        "vars": {
                            "flat": {
                                "$reduce": {
                                    "input": "$all_work_hours",
                                    "initialValue": [],
                                    "in": {"$concatArrays": ["$$value", "$$this"]},
                                }
                            }
                        },
                        "in": {
                            "$cond": [
                                {"$eq": [{"$size": "$$flat"}, 0]},
                                None,
                                {"$avg": "$$flat"},
                            ]
                        },
                    }
                },
            }
        },
        {"$sort": {"department": 1}},
    ]

    items = []
    for r in db.employees.aggregate(pipeline):
        avg = r.get("avg_work_hours")
        if avg is not None:
            avg = half_up_2(avg)
        items.append({
            "department": r["department"],
            "headcount": r["headcount"],
            "present_days": r["present_days"],
            "avg_work_hours": avg,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"],
        })
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    date_from, date_to, _, _ = month_bounds(month)

    pipeline: list[dict] = [
        {"$match": {"date": {"$gte": date_from, "$lte": date_to}}},
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "emp",
            }
        },
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline.extend([
        {
            "$project": {
                "emp_code": "$_id",
                "name": "$emp.name",
                "department": "$emp.department",
                "total_late_minutes": 1,
                "late_count": 1,
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ])

    rows = list(db.attendance_logs.aggregate(pipeline))

    items = []
    rank = 0
    prev_late = None
    for i, r in enumerate(rows):
        if prev_late is None or r["total_late_minutes"] != prev_late:
            rank = i + 1
        prev_late = r["total_late_minutes"]
        if len(items) >= limit:
            break
        items.append({
            "rank": rank,
            "emp_code": r["emp_code"],
            "name": r["name"],
            "department": r["department"],
            "total_late_minutes": r["total_late_minutes"],
            "late_count": r["late_count"],
        })

    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    if from_ > to:
        raise HTTPException(status_code=422, detail="from must be <= to")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(status_code=404, detail="department not found")

    start_d = date.fromisoformat(from_)
    end_d = date.fromisoformat(to)
    days = []
    d = start_d
    while d <= end_d:
        days.append(d)
        d += timedelta(days=1)

    emps = list(
        db.employees.find(
            {"department": department},
            {"emp_code": 1, "joined_on": 1, "_id": 0},
        )
    )
    emp_codes = [e["emp_code"] for e in emps]

    logs = list(
        db.attendance_logs.find(
            {
                "date": {"$gte": from_, "$lte": to},
                "emp_code": {"$in": emp_codes},
            },
            {
                "emp_code": 1,
                "date": 1,
                "status": 1,
                "half_day": 1,
                "late_minutes": 1,
            },
        )
    )
    log_by_date: dict[str, list] = {}
    for lg in logs:
        log_by_date.setdefault(lg["date"], []).append(lg)

    items = []
    rates: list[Optional[float]] = []
    for d in days:
        ds = d.isoformat()
        is_wd = d.weekday() < 5
        hc = sum(1 for e in emps if e["joined_on"] <= ds)
        day_logs = log_by_date.get(ds, [])
        present = 0.0
        late_c = 0
        for lg in day_logs:
            if lg["status"] in ("PRESENT", "WFH", "ON_DUTY"):
                present += 0.5 if lg.get("half_day") else 1.0
            if (lg.get("late_minutes") or 0) > 0:
                late_c += 1
        rate = None if hc == 0 else half_up_4(present / hc * 100)
        rates.append(rate)

        window = rates[-7:]
        valid = [x for x in window if x is not None]
        mav = half_up_4(sum(valid) / len(valid)) if valid else None

        items.append({
            "date": ds,
            "is_working_day": is_wd,
            "headcount": hc,
            "present_count": present,
            "late_count": late_c,
            "attendance_rate": rate,
            "moving_avg_7d": mav,
        })

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str = Path(
        ...,
        pattern=r"^(attendance_list|employee_monthly|department_summary|late_leaderboard|department_trend)$",
    ),
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        q: dict[str, Any] = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        skip = (page - 1) * page_size
        cursor = (
            db.attendance_logs.find(q)
            .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
            .skip(skip)
            .limit(page_size)
        )
        explain = cursor.explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month required")
        date_from, date_to, _, _ = month_bounds(month)
        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": date_from, "$lte": date_to},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "c": {"$sum": 1},
                }
            },
        ]
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month required")
        _, _, _, last = month_bounds(month)
        end_str = last.isoformat()
        emp_match: dict[str, Any] = {"joined_on": {"$lte": end_str}}
        if department:
            emp_match["department"] = department
        pipeline = [
            {"$match": emp_match},
            {"$group": {"_id": "$department", "headcount": {"$sum": 1}}},
        ]
        explain = db.command(
            "explain",
            {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month required")
        date_from, date_to, _, _ = month_bounds(month)
        pipeline = [
            {"$match": {"date": {"$gte": date_from, "$lte": date_to}}},
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {"$sort": {"total_late_minutes": -1}},
            {"$limit": limit},
        ]
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from and to required")
        pipeline = [
            {"$match": {"date": {"$gte": from_, "$lte": to}}},
            {"$group": {"_id": "$date", "c": {"$sum": 1}}},
        ]
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats",
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    raise HTTPException(404, "unknown endpoint")
