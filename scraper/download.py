"""
ESB Networks HDF data scraper.
Logs in to myaccount.esbnetworks.ie, downloads the latest HDF CSV,
and ingests new rows into SQLite — skipping any already stored.
"""

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).parent.parent / "config.json"
config = json.loads(CONFIG_PATH.read_text())

DATA_DIR = Path(config["data_dir"])
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "energy.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scraper] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ESB_LOGIN_URL   = "https://myaccount.esbnetworks.ie"
ESB_DOWNLOAD_URL = (
    "https://myaccount.esbnetworks.ie/api/consumption/download"
    "?mprn={mprn}&granularity=30"
)

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_csv() -> Path:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    username = config["esb"]["username"]
    password = config["esb"]["password"]
    mprn     = config["esb"]["mprn"]

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,          # ← watch it happen
            slow_mo=600,             # ← 600ms between actions
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        def snap(name):
            path = DATA_DIR / f"debug_{name}.png"
            page.screenshot(path=str(path))
            log.info(f"Screenshot → {path}")

        log.info("Navigating to ESB Networks...")
        page.goto(ESB_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)

        try:
            page.click("#onetrust-accept-btn-handler", timeout=6_000)
            log.info("Accepted cookie banner")
            page.wait_for_load_state("domcontentloaded")
        except PWTimeout:
            pass
        snap("01_after_cookie")

        log.info("Looking for Sign In button...")
        try:
            page.wait_for_selector(
                "a[href*='login'], a[href*='signin'], button:has-text('Sign in'), "
                "a:has-text('Sign in'), a:has-text('Log in'), button:has-text('Log in')",
                timeout=10_000,
            )
            page.click(
                "a[href*='login'], a[href*='signin'], button:has-text('Sign in'), "
                "a:has-text('Sign in'), a:has-text('Log in'), button:has-text('Log in')"
            )
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            log.info(f"After sign-in click, URL: {page.url}")
        except PWTimeout:
            log.info("No sign-in button found — may already be on login page")
        snap("02_after_signin_click")

        log.info("Waiting for email/username field...")
        page.wait_for_selector(
            '#signInName, input[name="loginfmt"], input[type="email"], '
            'input[name="email"], #email',
            timeout=20_000,
        )
        log.info(f"On login page: {page.url}")
        page.fill(
            '#signInName, input[name="loginfmt"], input[type="email"], '
            'input[name="email"], #email',
            username,
        )

        try:
            next_btn = page.locator(
                'button#next, button:has-text("Next"), input[value="Next"]'
            )
            if next_btn.count() > 0:
                log.info("Clicking Next (split email/password flow)...")
                next_btn.first.click()
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PWTimeout:
            pass

        log.info("Filling password...")
        page.wait_for_selector(
            '#password, input[type="password"], input[name="passwd"]',
            timeout=15_000,
        )
        page.fill(
            '#password, input[type="password"], input[name="passwd"]',
            password,
        )

        log.info("Submitting login form...")
        snap("03_before_submit")
        page.click(
            '#next, button[type="submit"], input[type="submit"], '
            'button:has-text("Sign in"), button:has-text("Log in")'
        )
        snap("04_after_submit")

        try:
            page.wait_for_url("*myaccount.esbnetworks.ie*", timeout=45_000)
            log.info("Redirected back to myaccount — login successful")
        except PWTimeout:
            log.error(f"Login may have failed — still on: {page.url}\nPage title: {page.title()}")
            snap("05_login_failed")
            browser.close()
            sys.exit(1)

# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_csv(csv_path: Path) -> int:
    """Parse CSV and insert rows not already in the DB. Returns new row count."""
    import sqlite3
    import csv

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    new_rows = 0
    skipped  = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mprn        = row["MPRN"].strip()
            meter_sn    = row["Meter Serial Number"].strip()
            read_type   = row["Read Type"].strip()
            read_value  = float(row["Read Value"])
            dt_str      = row["Read Date and End Time"].strip()  # "26-04-2026 03:30"

            # Normalise to ISO datetime string
            dt = datetime.strptime(dt_str, "%d-%m-%Y %H:%M")
            dt_iso = dt.strftime("%Y-%m-%d %H:%M:00")

            # "Active Import Interval (kWh)" → "import"
            # "Active Export Interval (kWh)" → "export"
            kind = "import" if "Import" in read_type else "export"

            try:
                cur.execute(
                    """INSERT INTO readings (mprn, meter_sn, reading_dt, kind, kwh)
                       VALUES (?, ?, ?, ?, ?)""",
                    (mprn, meter_sn, dt_iso, kind, read_value),
                )
                new_rows += 1
            except sqlite3.IntegrityError:
                skipped += 1

    conn.commit()
    conn.close()
    log.info(f"Ingested {new_rows} new rows, skipped {skipped} duplicates")
    return new_rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=== ESB Energy Scraper starting ===")
    try:
        csv_path = download_csv()
        new_rows = ingest_csv(csv_path)
        log.info(f"Done. {new_rows} new readings added to database.")
    except Exception as e:
        log.exception(f"Scraper failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
