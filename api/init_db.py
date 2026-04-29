"""
Initialise the SQLite database schema.
Safe to run multiple times (CREATE IF NOT EXISTS).
"""

import json
import sqlite3
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"
config = json.loads(CONFIG_PATH.read_text())

DATA_DIR = Path(config["data_dir"])
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "energy.db"


def init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 30-minute interval readings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            mprn       TEXT    NOT NULL,
            meter_sn   TEXT    NOT NULL,
            reading_dt TEXT    NOT NULL,   -- ISO "YYYY-MM-DD HH:MM:00" UTC+0 as supplied
            kind       TEXT    NOT NULL,   -- 'import' or 'export'
            kwh        REAL    NOT NULL,
            UNIQUE (mprn, reading_dt, kind)
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_readings_dt ON readings(reading_dt)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_readings_kind ON readings(reading_dt, kind)")

    conn.commit()
    conn.close()
    print(f"Database initialised: {DB_PATH}")


if __name__ == "__main__":
    init()
