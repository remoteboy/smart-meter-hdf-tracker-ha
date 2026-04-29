"""
ESB Energy Tracker API
FastAPI service exposing energy usage and cost data for Home Assistant.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.tariff import daily_summary, load_config

# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def get_db():
    config = load_config()
    db_path = Path(config["data_dir"]) / "energy.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

# ---------------------------------------------------------------------------
app = FastAPI(
    title="ESB Energy Tracker",
    description="Smart meter import/export data with Irish tariff cost calculations",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_rows(conn, start_date: date, end_date: date) -> list[dict]:
    """Fetch all readings between two dates (inclusive)."""
    cur = conn.execute(
        """SELECT reading_dt, kind, kwh
           FROM readings
           WHERE reading_dt >= ? AND reading_dt < ?
           ORDER BY reading_dt""",
        (
            start_date.strftime("%Y-%m-%d 00:00:00"),
            (end_date + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"),
        ),
    )
    return [dict(r) for r in cur.fetchall()]


def rows_for_day(conn, day: date) -> list[dict]:
    return fetch_rows(conn, day, day)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/status")
def status():
    """Database stats — useful for HA to confirm data freshness."""
    config = load_config()
    conn = get_db()
    row = conn.execute(
        "SELECT MAX(reading_dt) as latest, MIN(reading_dt) as earliest, COUNT(*) as total FROM readings"
    ).fetchone()
    conn.close()
    return {
        "earliest_reading": row["earliest"],
        "latest_reading":   row["latest"],
        "total_readings":   row["total"],
        "config": {
            "night_start": config["night_hours"]["start"],
            "night_end":   config["night_hours"]["end"],
            "vat_rate":    config.get("vat_rate", 0),
            "tariffs":     config["tariffs"],
        },
    }


@app.get("/day/{date_str}")
def day_summary(date_str: str):
    """
    Cost and usage for a single day.
    date_str: YYYY-MM-DD
    """
    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date. Use YYYY-MM-DD.")

    config = load_config()
    conn   = get_db()
    rows   = rows_for_day(conn, day)
    conn.close()

    if not rows:
        raise HTTPException(404, f"No data for {date_str}")

    summary = daily_summary(rows, config)
    summary["date"] = date_str
    return summary


@app.get("/today")
def today():
    """Shortcut: today's summary."""
    from zoneinfo import ZoneInfo
    today_ie = datetime.now(ZoneInfo("Europe/Dublin")).date()
    config = load_config()
    conn   = get_db()
    rows   = rows_for_day(conn, today_ie)
    conn.close()

    if not rows:
        # Return zeros rather than 404 — HA sensors prefer this
        return {
            "date": today_ie.isoformat(),
            "import_day_kwh": 0, "import_night_kwh": 0, "export_kwh": 0,
            "import_day_cost": 0, "import_night_cost": 0, "export_credit": 0,
            "standing_charges": 0, "vat": 0, "total_cost": 0,
            "note": "No data yet for today — will populate after nightly sync",
        }

    summary = daily_summary(rows, config)
    summary["date"] = today_ie.isoformat()
    return summary


@app.get("/yesterday")
def yesterday():
    """Shortcut: yesterday's summary."""
    from zoneinfo import ZoneInfo
    yest = datetime.now(ZoneInfo("Europe/Dublin")).date() - timedelta(days=1)
    config = load_config()
    conn   = get_db()
    rows   = rows_for_day(conn, yest)
    conn.close()

    if not rows:
        raise HTTPException(404, "No data for yesterday")

    summary = daily_summary(rows, config)
    summary["date"] = yest.isoformat()
    return summary


@app.get("/range")
def date_range(
    start: str = Query(..., description="YYYY-MM-DD"),
    end:   str = Query(..., description="YYYY-MM-DD"),
):
    """
    Per-day breakdown for a date range.
    Returns a list of daily summaries.
    """
    try:
        start_d = date.fromisoformat(start)
        end_d   = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD for start and end.")

    if (end_d - start_d).days > 366:
        raise HTTPException(400, "Range cannot exceed 366 days.")

    config = load_config()
    conn   = get_db()
    all_rows = fetch_rows(conn, start_d, end_d)
    conn.close()

    # Group by date
    by_day: dict[str, list] = {}
    for r in all_rows:
        day_key = r["reading_dt"][:10]
        by_day.setdefault(day_key, []).append(r)

    results = []
    d = start_d
    while d <= end_d:
        key = d.isoformat()
        day_rows = by_day.get(key, [])
        if day_rows:
            s = daily_summary(day_rows, config)
            s["date"] = key
            results.append(s)
        d += timedelta(days=1)

    return results


@app.get("/month/{year_month}")
def month_summary(year_month: str):
    """
    Aggregate for a full month.
    year_month: YYYY-MM
    """
    try:
        year, month = [int(x) for x in year_month.split("-")]
        start_d = date(year, month, 1)
        # First day of next month
        if month == 12:
            end_d = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_d = date(year, month + 1, 1) - timedelta(days=1)
    except (ValueError, AttributeError):
        raise HTTPException(400, "Use YYYY-MM format.")

    config = load_config()
    conn   = get_db()
    rows   = fetch_rows(conn, start_d, end_d)
    conn.close()

    if not rows:
        raise HTTPException(404, f"No data for {year_month}")

    summary = daily_summary(rows, config)
    summary["month"] = year_month
    summary["days_with_data"] = len(set(r["reading_dt"][:10] for r in rows))
    return summary


@app.get("/billing")
def billing_period(
    start: str = Query(..., description="YYYY-MM-DD — start of billing period"),
    end:   str = Query(..., description="YYYY-MM-DD — end of billing period"),
):
    """
    Estimated bill for an arbitrary billing period (e.g. your two-monthly ESB bill).
    Returns per-day breakdown plus totals.
    """
    try:
        start_d = date.fromisoformat(start)
        end_d   = date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD.")

    config = load_config()
    conn   = get_db()
    rows   = fetch_rows(conn, start_d, end_d)
    conn.close()

    if not rows:
        raise HTTPException(404, "No data for that period.")

    totals = daily_summary(rows, config)
    totals["period_start"]     = start
    totals["period_end"]       = end
    totals["days_with_data"]   = len(set(r["reading_dt"][:10] for r in rows))

    return totals


@app.get("/ha/sensors")
def ha_sensors():
    """
    Single endpoint for Home Assistant to poll.
    Returns a flat dict of sensor values for easy template sensor mapping.
    """
    from zoneinfo import ZoneInfo
    ie_now  = datetime.now(ZoneInfo("Europe/Dublin"))
    today   = ie_now.date()
    yest    = today - timedelta(days=1)
    month_s = today.replace(day=1)

    config = load_config()
    conn   = get_db()

    def safe_summary(rows):
        if not rows:
            return {k: 0 for k in [
                "import_day_kwh","import_night_kwh","export_kwh",
                "import_day_cost","import_night_cost","export_credit",
                "standing_charges","vat","total_cost"
            ]}
        return daily_summary(rows, config)

    today_rows = rows_for_day(conn, today)
    yest_rows  = rows_for_day(conn, yest)
    month_rows = fetch_rows(conn, month_s, today)

    t  = safe_summary(today_rows)
    y  = safe_summary(yest_rows)
    m  = safe_summary(month_rows)

    # Latest reading time
    latest = conn.execute(
        "SELECT MAX(reading_dt) as dt FROM readings"
    ).fetchone()["dt"]
    conn.close()

    return {
        # Today
        "today_import_kwh":        round(t["import_day_kwh"] + t["import_night_kwh"], 3),
        "today_import_day_kwh":    t["import_day_kwh"],
        "today_import_night_kwh":  t["import_night_kwh"],
        "today_export_kwh":        t["export_kwh"],
        "today_cost":              t["total_cost"],
        "today_import_cost":       round(t["import_day_cost"] + t["import_night_cost"], 4),
        "today_export_credit":     t["export_credit"],

        # Yesterday
        "yesterday_import_kwh":    round(y["import_day_kwh"] + y["import_night_kwh"], 3),
        "yesterday_export_kwh":    y["export_kwh"],
        "yesterday_cost":          y["total_cost"],
        "yesterday_import_cost":   round(y["import_day_cost"] + y["import_night_cost"], 4),
        "yesterday_export_credit": y["export_credit"],

        # Month to date
        "month_import_kwh":        round(m["import_day_kwh"] + m["import_night_kwh"], 3),
        "month_export_kwh":        m["export_kwh"],
        "month_cost":              m["total_cost"],
        "month_import_cost":       round(m["import_day_cost"] + m["import_night_cost"], 4),
        "month_export_credit":     m["export_credit"],

        # Meta
        "data_as_of":              latest,
        "updated_at":              ie_now.isoformat(),
    }
