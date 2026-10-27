"""
Tariff engine.

Handles:
- Multiple tariff periods (price changes over time)
- DST-aware time-band classification for Irish time (Europe/Dublin)
- Flexible rate bands per tariff entry:
    Standard (day + night):
        import_day_rate, import_night_rate
    With Night Boost (e.g. Electric Ireland Home Dual + Night Boost):
        + import_night_boost_rate, night_boost_hours: {start, end}
    With Peak (future plans, e.g. smart tariffs):
        + import_peak_rate, peak_hours: {start, end}
    Boost and peak windows are carved out of day/night as appropriate.
- Import, export rates; standing charges; PSO levy — all date-ranged
- VAT applied to import costs and standing charges (not export credit)

Rate classification priority (highest to lowest):
  1. boost window  (subset of night, if configured)
  2. peak window   (subset of day, if configured — not yet used)
  3. night window  (night_hours)
  4. day           (everything else)
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
    sc = config.get("standing_charges", [])
    if isinstance(sc, list):
        return _latest_entry(sc, reading_date).get("import_daily", 0.0)
    return sc.get("import_daily", 0.0)


def get_pso_levy(reading_date: date, config: dict) -> float:
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


def _parse_time(s: str) -> time:
    """Parse 'HH:MM' into a time object."""
    h, m = s.split(":")
    return time(int(h), int(m))


def _in_window(t: time, window_start: time, window_end: time) -> bool:
    """True if t falls within [window_start, window_end), handling midnight wrap."""
    t = t.replace(second=0, microsecond=0)
    if window_start > window_end:          # spans midnight
        return t >= window_start or t < window_end
    elif window_start == window_end:       # zero-width → never
        return False
    else:
        return window_start <= t < window_end


def classify_interval(dt_local: datetime, tariff: dict, config: dict) -> str:
    """
    Return the rate band name for a single 30-min interval.

    Possible values: 'boost', 'peak', 'night', 'day'

    Priority order:
      boost > peak > night > day

    This means boost (02:00–04:00) is always priced at the boost rate even
    though it also falls inside the night window.  Peak (when configured)
    is carved out of the day window similarly.
    """
    t = dt_local.time()

    night_start = _parse_time(config["night_hours"]["start"])
    night_end   = _parse_time(config["night_hours"]["end"])

    # --- Boost window (optional) ---
    boost_hours = tariff.get("night_boost_hours") or config.get("night_boost_hours")
    if boost_hours and tariff.get("import_night_boost_rate") is not None:
        boost_start = _parse_time(boost_hours["start"])
        boost_end   = _parse_time(boost_hours["end"])
        if _in_window(t, boost_start, boost_end):
            return "boost"

    # --- Peak window (optional, future use) ---
    peak_hours = tariff.get("peak_hours") or config.get("peak_hours")
    if peak_hours and tariff.get("import_peak_rate") is not None:
        peak_start = _parse_time(peak_hours["start"])
        peak_end   = _parse_time(peak_hours["end"])
        if _in_window(t, peak_start, peak_end):
            return "peak"

    # --- Night window ---
    if _in_window(t, night_start, night_end):
        return "night"

    return "day"


def daily_summary(rows: list[dict], config: dict) -> dict:
    """
    Given a list of reading dicts for one or more calendar days, return cost breakdown.
    Each dict: {reading_dt, kind, kwh}

    Returned keys always present:
      import_day_kwh, import_night_kwh, import_boost_kwh, import_peak_kwh,
      export_kwh,
      import_day_cost, import_night_cost, import_boost_cost, import_peak_cost,
      export_credit, standing_charge, pso_levy, vat, total_cost

    Boost and peak fields are 0.0 when not in use — downstream consumers
    can always safely reference them.
    """
    vat_rate = config.get("vat_rate", 0.09)

    import_day_kwh    = 0.0
    import_night_kwh  = 0.0
    import_boost_kwh  = 0.0
    import_peak_kwh   = 0.0
    export_kwh        = 0.0

    import_day_cost   = 0.0
    import_night_cost = 0.0
    import_boost_cost = 0.0
    import_peak_cost  = 0.0
    export_credit     = 0.0

    for r in rows:
        dt_local = datetime.strptime(r["reading_dt"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=TZ_IRELAND
        )
        d = dt_local.date()
        tariff = get_tariff(d, config)

        if r["kind"] == "import":
            band = classify_interval(dt_local, tariff, config)
            kwh  = r["kwh"]

            if band == "boost":
                import_boost_kwh  += kwh
                import_boost_cost += kwh * tariff["import_night_boost_rate"]
            elif band == "peak":
                import_peak_kwh  += kwh
                import_peak_cost += kwh * tariff["import_peak_rate"]
            elif band == "night":
                import_night_kwh  += kwh
                import_night_cost += kwh * tariff["import_night_rate"]
            else:
                import_day_kwh  += kwh
                import_day_cost += kwh * tariff["import_day_rate"]
        else:
            export_kwh    += r["kwh"]
            export_credit += r["kwh"] * tariff["export_rate"]

    # Standing charges and PSO — one charge per unique calendar day in the dataset
    unique_dates = set(r["reading_dt"][:10] for r in rows)
    standing = sum(get_standing_charge(date.fromisoformat(d), config) for d in unique_dates)
    pso      = sum(get_pso_levy(date.fromisoformat(d), config) for d in unique_dates)

    # VAT applies to import costs and standing charges, not export credit
    import_subtotal  = import_day_cost + import_night_cost + import_boost_cost + import_peak_cost
    charges_subtotal = standing + pso
    vat   = (import_subtotal + charges_subtotal) * vat_rate
    total = import_subtotal + charges_subtotal + vat - export_credit

    return {
        "import_day_kwh":    round(import_day_kwh,    3),
        "import_night_kwh":  round(import_night_kwh,  3),
        "import_boost_kwh":  round(import_boost_kwh,  3),
        "import_peak_kwh":   round(import_peak_kwh,   3),
        "export_kwh":        round(export_kwh,         3),
        "import_day_cost":   round(import_day_cost,   4),
        "import_night_cost": round(import_night_cost, 4),
        "import_boost_cost": round(import_boost_cost, 4),
        "import_peak_cost":  round(import_peak_cost,  4),
        "export_credit":     round(export_credit,     4),
        "standing_charge":   round(standing,          4),
        "pso_levy":          round(pso,               4),
        "vat":               round(vat,               4),
        "total_cost":        round(total,             4),
    }
