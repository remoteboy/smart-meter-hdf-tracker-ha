"""
Ingest a manually downloaded HDF CSV into the database.
Run this once to load your historical data.

Usage:
  python -m scraper.ingest_csv /path/to/HDF_calckWh_*.csv
"""

import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from scraper.download import ingest_csv
from api.init_db import init

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scraper.ingest_csv <path-to-csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print("Initialising database...")
    init()

    print(f"Ingesting {csv_path.name}...")
    new_rows = ingest_csv(csv_path)
    print(f"Done. {new_rows} new rows added.")
