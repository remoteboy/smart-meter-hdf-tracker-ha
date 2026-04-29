"""
Tariff engine.

Handles:
- Multiple tariff periods (price changes over time)
- DST-aware night/day split for Irish time (Europe/Dublin)
- Night rate window spanning midnight (e.g. 23:00 → 08:00)
- Import day/night and export rates
- Optional VAT and standing charges
"""

from datetime import date, datetime, time, timedelta
from typing import Optional
import json
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_IRELAND = ZoneInfo("Europe/Dublin")
TZ_UTC     = ZoneInfo("UTC")

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def get_tariff(reading_date: date, config: dict) -> dict:
    """Return the applicable tariff for a given date (most recent effective_from ≤ date)."""
    tariffs = sorted(
        config["tariffs"],
        key=lambda t: t["effective_from"],
        reverse=True,
    )
    date_str = reading_date.isoformat()
    for t in tariffs:
        if t["effective_from"] <= date_str:
            return t
    # Fallback: oldest tariff
    return tariffs[-1]


def is_night_rate(dt_local: datetime, night_start: time, night_end: time) -> bool:
    """
    Returns True if the local datetime falls within the night rate window.
    Handles windows that span midnight (e.g. 23:00 → 08:00).
    """
    t = dt_local.time().replace(second=0, microsecond=0)
    if night_start > night_end:
        # Spans midnight: night if t >= start OR t < end
        return t >= night_start or t < night_end
    else:
        return night_start <= t < night_end


def slot_cost(reading_dt_str: str, kind: str, kwh: float, config: dict) -> float:
    """
    Calculate the cost (€) for a single 30-minute reading slot.
    reading_dt_str: "YYYY-MM-DD HH:MM:00" — treated as Irish local time
                    (ESB timestamps are in Irish local time).
    kind: 'import' or 'export'
    Returns a positive cost for import, negative (credit) for export.
    """
    # ESB timestamps are Irish local time
    dt_local = datetime.strptime(reading_dt_str, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=TZ_IRELAND
    )
    reading_date = dt_local.date()
    tariff = get_tariff(reading_date, config)

    night_start = time(*[int(x) for x in config["night_hours"]["start"].split(":")])
    night_end   = time(*[int(x) for x in config["night_hours"]["end"].split(":")])

    if kind == "import":
        if is_night_rate(dt_local, night_start, night_end):
            rate = tariff["import_night_rate"]
        else:
            rate = tariff["import_day_rate"]
        return kwh * rate
    else:  # export
        rate = tariff["export_rate"]
        return -(kwh * rate)  # negative = credit


def daily_summary(rows: list[dict], config: dict) -> dict:
    """
    Given a list of reading dicts for a single day, compute cost breakdown.
    Each dict: {reading_dt, kind, kwh}
    """
    import_day_kwh   = 0.0
    import_night_kwh = 0.0
    export_kwh       = 0.0
    import_day_cost  = 0.0
    import_night_cost= 0.0
    export_credit    = 0.0

    night_start = time(*[int(x) for x in config["night_hours"]["start"].split(":")])
    night_end   = time(*[int(x) for x in config["night_hours"]["end"].split(":")])

    for r in rows:
        dt_local = datetime.strptime(r["reading_dt"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=TZ_IRELAND
        )
        tariff = get_tariff(dt_local.date(), config)

        if r["kind"] == "import":
            if is_night_rate(dt_local, night_start, night_end):
                import_night_kwh  += r["kwh"]
                import_night_cost += r["kwh"] * tariff["import_night_rate"]
            else:
                import_day_kwh  += r["kwh"]
                import_day_cost += r["kwh"] * tariff["import_day_rate"]
        else:
            export_kwh    += r["kwh"]
            export_credit += r["kwh"] * tariff["export_rate"]

    standing = config.get("standing_charges", {})
    vat_rate = config.get("vat_rate", 0.0)

    standing_import = standing.get("import_daily", 0.0)
    standing_export = standing.get("export_daily", 0.0)

    subtotal = (
        import_day_cost + import_night_cost
        - export_credit
        + standing_import + standing_export
    )
    vat = subtotal * vat_rate
    total = subtotal + vat

    return {
        "import_day_kwh":    round(import_day_kwh,   3),
        "import_night_kwh":  round(import_night_kwh,  3),
        "export_kwh":        round(export_kwh,         3),
        "import_day_cost":   round(import_day_cost,    4),
        "import_night_cost": round(import_night_cost,  4),
        "export_credit":     round(export_credit,      4),
        "standing_charges":  round(standing_import + standing_export, 4),
        "vat":               round(vat,               4),
        "total_cost":        round(total,              4),
    }
