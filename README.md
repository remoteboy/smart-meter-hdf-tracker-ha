# Smart Meter HDF Tracker

A self-hosted smart meter data pipeline for ESB Networks (Ireland) customers. Downloads your 30-minute interval data nightly, stores it in SQLite, exposes a REST API with accurate Irish tariff cost calculations, and integrates with Home Assistant.

## Features

- **Nightly automated download** from myaccount.esbnetworks.ie via Playwright
- **Accurate cost calculations** with day/night rate split, fully DST-aware (Europe/Dublin)
- **Date-ranged tariff history** — add new rate entries as prices change; historical data is always costed at the rate that applied at the time
- **Separate standing charge, PSO levy and unit rates** — each independently date-ranged
- **Export credit** tracking for solar / micro-generation customers
- **VAT handling** — applied correctly to import and standing charges, not export credit
- **REST API** with daily, monthly, billing period and date range endpoints
- **Home Assistant integration** — REST sensors for today / yesterday / month-to-date
- **Web dashboard** — dark-themed, accessible, served from the same FastAPI process
- **2captcha integration** for automated CAPTCHA solving on login
- Runs on a **lightweight Debian LXC** on Proxmox (~200MB RAM idle)

---

## Requirements

- Proxmox VE host (or any Debian/Ubuntu Linux machine)
- ESB Networks account at [myaccount.esbnetworks.ie](https://myaccount.esbnetworks.ie)
- [2captcha](https://2captcha.com) account with credit (~€2–3/month at one solve per day)
- Home Assistant (optional)

---

## Architecture

```
ESB Networks website
        │  Playwright scraper — nightly 02:30, systemd timer
        ▼
  SQLite database          /opt/esb-energy/data/energy.db
        │
        ▼
  FastAPI REST API         http://<LXC-IP>:8000
        │
        ├── /docs           Interactive API documentation
        ├── /dashboard/     Web dashboard
        └── /ha/sensors     Home Assistant polling endpoint

  Home Assistant           REST sensors, polls hourly
```

---

## Installation

### 1. Create a Proxmox LXC

Edit `setup/create-lxc.sh` to set your container ID, IP address, storage pool and gateway, then on the Proxmox host:

```bash
bash setup/create-lxc.sh
```

### 2. Copy the app into the container

```bash
tar czf /tmp/esb-energy.tar.gz esb-energy/
pct push <CTID> /tmp/esb-energy.tar.gz /tmp/esb-energy.tar.gz
pct exec <CTID> -- bash -c "cd /opt && tar xzf /tmp/esb-energy.tar.gz"
```

### 3. Configure

```bash
pct exec <CTID> -- nano /opt/esb-energy/config.json
```

Set your ESB credentials, MPRN, tariff rates and 2captcha API key. See [Configuration](#configuration).

### 4. Install

```bash
pct exec <CTID> -- bash /opt/esb-energy/setup/install.sh
```

### 5. Load historical data

Download your full history CSV from ESB Networks (Downloads → 30-minute readings in calculated kWh), then:

```bash
pct push <CTID> HDF_calckWh_*.csv /tmp/history.csv
pct exec <CTID> -- bash -c "cd /opt/esb-energy && \
    /opt/esb-energy/venv/bin/python -m scraper.ingest_csv /tmp/history.csv"
```

ESB provides up to two years of history in this file.

### 6. Verify

```bash
curl http://<LXC-IP>:8000/status
curl http://<LXC-IP>:8000/yesterday
```

Open `http://<LXC-IP>:8000/dashboard/` to see the web dashboard.

---

## Configuration

`config.json` — all fields:

```json
{
  "esb": {
    "username": "your@email.com",
    "password": "yourpassword",
    "mprn": "10001234567"
  },

  "tariffs": [
    {
      "_comment": "Add a new entry whenever unit rates change.",
      "effective_from": "2025-10-17",
      "import_day_rate":   0.2310,
      "import_night_rate": 0.1160,
      "export_rate":       0.1850
    }
  ],

  "standing_charges": [
    {
      "_comment": "Daily network standing charge ex VAT.",
      "effective_from": "2025-10-17",
      "import_daily": 0.8911
    }
  ],

  "pso_levy": [
    {
      "_comment": "PSO levy stored as monthly figure ex VAT. Changes each October.",
      "effective_from": "2025-10-01",
      "monthly": 1.46
    }
  ],

  "night_hours": {
    "_comment": "Night rate window in Irish clock time. DST handled automatically.",
    "start": "23:00",
    "end":   "08:00"
  },

  "vat_rate": 0.09,
  "two_captcha_api_key": "your_key_here",
  "data_dir": "/opt/esb-energy/data"
}
```

All rates are **ex VAT**. To add a price change, add a new entry to the relevant array — older entries are preserved and used to cost historical data correctly.

---

## API Reference

Interactive docs at `http://<LXC-IP>:8000/docs`.

| Endpoint | Description |
|---|---|
| `GET /status` | DB stats and config summary |
| `GET /today` | Today's usage and cost |
| `GET /yesterday` | Yesterday's usage and cost |
| `GET /day/YYYY-MM-DD` | Specific day |
| `GET /range?start=…&end=…` | Per-day list for a date range |
| `GET /month/YYYY-MM` | Full month aggregate |
| `GET /billing?start=…&end=…` | Arbitrary billing period total |
| `GET /ha/sensors` | Flat dict for Home Assistant |
| `GET /dashboard/` | Web dashboard |

### Example — `/day/2026-04-25`

```json
{
  "date": "2026-04-25",
  "import_day_kwh": 0.523,
  "import_night_kwh": 6.245,
  "export_kwh": 12.724,
  "import_day_cost": 0.1208,
  "import_night_cost": 0.7244,
  "export_credit": 2.3539,
  "standing_charge": 0.8911,
  "pso_levy": 0.048,
  "vat": 0.1606,
  "total_cost": -0.409
}
```

A negative `total_cost` means you exported more than you spent — in credit for that day.

---

## Home Assistant Integration

Add `ha/configuration_snippet.yaml` to your `configuration.yaml` (update the IP), restart HA.

Key sensors created:

| Entity | Description |
|---|---|
| `sensor.esb_yesterday_net_cost` | Yesterday's net cost inc all charges |
| `sensor.esb_month_net_cost` | Month-to-date net cost |
| `sensor.esb_month_export_credit` | Month-to-date export credit |
| `sensor.esb_month_import_kwh` | Month-to-date grid import |
| `sensor.esb_month_export_kwh` | Month-to-date solar export |
| `sensor.esb_data_as_of` | Timestamp of most recent reading |

A full Lovelace dashboard YAML is in `ha/lovelace-complete.yaml`.

---

## Operations

```bash
# Service status
systemctl status esb-api
systemctl status esb-scraper.timer

# Logs
journalctl -u esb-api -f
journalctl -u esb-scraper -n 50

# Run scraper manually
cd /opt/esb-energy && /opt/esb-energy/venv/bin/python -m scraper.download

# Restart API after config change
systemctl restart esb-api
```

---

## How the scraper works

ESB Networks uses Azure AD B2C authentication with reCAPTCHA v2 bot detection. The scraper:

1. Launches headless Chromium via Playwright and navigates to the B2C OAuth URL
2. Fills credentials
3. If reCAPTCHA appears, sends the site key to 2captcha (~30s for a solution), injects the token and continues
4. Saves the authenticated session cookies to disk
5. On subsequent runs, reuses saved cookies — login and CAPTCHA are skipped until the session expires
6. Navigates to the consumption page, reveals the download panel via JavaScript, and captures the CSV file
7. Ingests the CSV into SQLite, skipping any rows already present

ESB limits fresh logins to approximately 3 per day before triggering CAPTCHA, so session persistence is important. Sessions typically last several days.

---

## File structure

```
esb-energy/
├── config.json                     Your configuration (keep private)
├── scraper/
│   ├── download.py                 Scraper — login, download, ingest
│   └── ingest_csv.py               One-shot historical CSV importer
├── api/
│   ├── main.py                     FastAPI application
│   ├── tariff.py                   Cost calculation engine
│   └── init_db.py                  Database schema
├── dashboard/
│   └── index.html                  Web dashboard
├── ha/
│   ├── configuration_snippet.yaml  HA sensor definitions
│   └── lovelace-complete.yaml      HA Lovelace dashboard
└── setup/
    ├── create-lxc.sh               Proxmox LXC creation
    ├── install.sh                  App installation
    ├── esb-api.service             systemd service — API
    ├── esb-scraper.service         systemd service — scraper
    └── esb-scraper.timer           systemd timer — nightly 02:30
```

---

## Troubleshooting

**Permission denied on cookies file**
The cookie file was created by root. Fix:
```bash
chown -R esb:esb /opt/esb-energy/data
```

**Session expires every night**
Sessions should last days. If expiring nightly, the permissions issue above is likely the cause — the scraper can't save the fresh cookies so it has to log in from scratch every time.

**CAPTCHA on every run**
Once sessions persist correctly, CAPTCHA is rare. If it triggers every night, fix permissions first.

**Download button not found**
ESB may have updated their page. Check the debug screenshots saved to `/opt/esb-energy/data/`.

**API shows stale data**
ESB data is typically 24–48 hours behind. The most recent reading is usually from two days ago — this is normal.


---

## Docker / Unraid

A Docker image is provided for users not running Proxmox.

### Quick start with Docker Compose

```bash
git clone https://github.com/yourusername/esb-energy
cd esb-energy
cp docker-compose.yml docker-compose.override.yml
# Edit docker-compose.override.yml with your credentials
docker compose up -d
```

Or set environment variables directly:

```bash
docker run -d \
  --name esb-energy \
  --restart unless-stopped \
  -p 8000:8000 \
  -v ./data:/data \
  -e TZ=Europe/Dublin \
  -e ESB_USERNAME=your@email.com \
  -e ESB_PASSWORD=yourpassword \
  -e ESB_MPRN=10001234567 \
  -e TWO_CAPTCHA_KEY=your_key \
  esb-energy:latest
```

### Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ESB_USERNAME` | Yes | — | ESB Networks login email |
| `ESB_PASSWORD` | Yes | — | ESB Networks password |
| `ESB_MPRN` | Yes | — | Meter Point Reference Number |
| `TWO_CAPTCHA_KEY` | Yes | — | 2captcha.com API key |
| `TZ` | Yes | — | Timezone e.g. `Europe/Dublin` |
| `SCRAPER_TIME` | No | `02:30` | Nightly download time (24h local) |
| `RUN_ON_START` | No | `false` | Download data on container start |
| `NIGHT_START` | No | `23:00` | Night rate window start |
| `NIGHT_END` | No | `08:00` | Night rate window end |
| `VAT_RATE` | No | `0.09` | VAT rate as decimal |
| `DATA_DIR` | No | `/data` | Data directory inside container |

### Tariff configuration in Docker

Unit rates, standing charges and PSO levy can't be set via environment variables (they're date-ranged). Mount a `config.json` instead:

```bash
cp config.example.json config.json
# Edit config.json with your tariff history
docker run ... -v ./config.json:/app/config.json:ro esb-energy:latest
```

See `config.example.json` for the full format.

### Loading historical data in Docker

```bash
# Copy your downloaded CSV into the container and ingest it
docker cp HDF_calckWh_*.csv esb-energy:/tmp/history.csv
docker exec esb-energy python -m scraper.ingest_csv /tmp/history.csv
```

### Unraid

1. In Community Applications, search for **ESB Energy**
2. Fill in your ESB credentials, MPRN and 2captcha key
3. Set the appdata path and click Install
4. Open `http://<unraid-ip>:8000/dashboard/` to verify

Alternatively, install manually via the Docker tab using the template in `unraid/esb-energy.xml`.

---

## Contributing

Please do not submit pull requests at this time. This is very much an early beta. 

---

## Licence

MIT

---

## Credits
This project was built in collaboration with Claude (Anthropic's AI assistant), which designed and wrote the majority of the code across a long session covering the scraper, tariff engine, API, dashboard, Home Assistant integration, and Docker/Unraid support.
