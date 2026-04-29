"""
Tariff engine.

Handles:
- Multiple tariff periods (price changes over time)
- DST-aware night/day split for Irish time (Europe/Dublin)
- Night rate window spanning midnight (e.g. 23:00 → 08:00)
- Import day/night and export rates
- Standing charges, PSO levy — all date-ranged independently
- VAT applied to import costs and standing charges (not export credit)
"""

from datetime import date, datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_IRELAND = ZoneInfo("Europe/Dublin")
CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _latest_entry(entries: list, reading_date: date) -> dict:
    """Return the most recent entry whose effective_from <= reading_date."""
    date_str = reading_date.isoformat()
    for entry in sorted(entries, key=lambda x: x["effective_from"], reverse=True):
        if entry["effective_from"] <= date_str:
            return entry
    return sorted(entries, key=lambda x: x["effective_from"])[0]


def get_tariff(reading_date: date, config: dict) -> dict:
    """Applicable unit rates for a given date."""
    return _latest_entry(config["tariffs"], reading_date)


def get_standing_charge(reading_date: date, config: dict) -> float:
    """
    Daily standing charge (ex VAT) for a given date.
    Supports both old dict format and new date-ranged list format.
    """
    sc = config.get("standing_charges", [])
    if isinstance(sc, list):
        return _latest_entry(sc, reading_date).get("import_daily", 0.0)
    # Legacy single dict
    return sc.get("import_daily", 0.0)


def get_pso_levy(reading_date: date, config: dict) -> float:
    """
    Daily PSO levy (ex VAT) for a given date.
    Supports both old dict format and new date-ranged list format.
    monthly value is stored, converted to daily here.
    """
    pso = config.get("pso_levy", [])
    if isinstance(pso, list):
        if not pso:
            return 0.0
        entry = _latest_entry(pso, reading_date)
    elif isinstance(pso, dict):
        entry = pso
    else:
        return 0.0

    monthly = entry.get("monthly", 0.0)
    return round(monthly * 12 / 365, 6)


def is_night_rate(dt_local: datetime, night_start: time, night_end: time) -> bool:
    t = dt_local.time().replace(second=0, microsecond=0)
    if night_start > night_end:
        return t >= night_start or t < night_end
    return night_start <= t < night_end


def daily_summary(rows: list[dict], config: dict) -> dict:
    """
    Given a list of reading dicts for a single calendar day, return cost breakdown.
    Each dict: {reading_dt, kind, kwh}
    """
    night_start = time(*[int(x) for x in config["night_hours"]["start"].split(":")])
    night_end   = time(*[int(x) for x in config["night_hours"]["end"].split(":")])
    vat_rate    = config.get("vat_rate", 0.09)

    import_day_kwh    = 0.0
    import_night_kwh  = 0.0
    export_kwh        = 0.0
    import_day_cost   = 0.0
    import_night_cost = 0.0
    export_credit     = 0.0

    # Use the date of the first row for standing charges
    first_date = None

    for r in rows:
        dt_local = datetime.strptime(r["reading_dt"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=TZ_IRELAND
        )
        d = dt_local.date()
        if first_date is None:
            first_date = d

        tariff = get_tariff(d, config)

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

    if first_date is None:
        first_date = date.today()

    # Sum standing charge and PSO across each unique day in the dataset
    unique_dates = set()
    for r in rows:
        unique_dates.add(r["reading_dt"][:10])

    standing = sum(get_standing_charge(date.fromisoformat(d), config) for d in unique_dates)
    pso      = sum(get_pso_levy(date.fromisoformat(d), config) for d in unique_dates)

    # VAT applies to import costs and standing charges, not export credit
    import_subtotal  = import_day_cost + import_night_cost
    charges_subtotal = standing + pso
    vat = (import_subtotal + charges_subtotal) * vat_rate
    total = import_subtotal + charges_subtotal + vat - export_credit

    return {
        "import_day_kwh":    round(import_day_kwh,    3),
        "import_night_kwh":  round(import_night_kwh,  3),
        "export_kwh":        round(export_kwh,          3),
        "import_day_cost":   round(import_day_cost,    4),
        "import_night_cost": round(import_night_cost,  4),
        "export_credit":     round(export_credit,      4),
        "standing_charge":   round(standing,           4),
        "pso_levy":          round(pso,                4),
        "vat":               round(vat,               4),
        "total_cost":        round(total,              4),
    }