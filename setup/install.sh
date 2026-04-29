#!/bin/bash
# Run inside the LXC container after copying app files
# pct exec <CTID> -- bash /opt/smart-meter-hdf-tracker-ha/setup/install.sh

set -e
APP_DIR=/opt/smart-meter-hdf-tracker-ha

echo "==> Setting up Python virtual environment..."
python3 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate

pip install -q --upgrade pip
pip install -q \
  playwright \
  fastapi \
  uvicorn[standard] \
  python-dateutil \
  pytz

# Install Playwright browser (uses system chromium in LXC)
python3 -m playwright install chromium 2>/dev/null || true

echo "==> Initialising database..."
python3 $APP_DIR/api/init_db.py

echo "==> Installing systemd services..."
cp $APP_DIR/setup/esb-api.service /etc/systemd/system/
cp $APP_DIR/setup/esb-scraper.service /etc/systemd/system/
cp $APP_DIR/setup/esb-scraper.timer /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now esb-api.service
systemctl enable --now esb-scraper.timer

echo ""
echo "==> Installation complete!"
echo "    API running at: http://$(hostname -I | awk '{print $1}'):8000"
echo "    Config file   : $APP_DIR/config.json"
echo "    Logs          : journalctl -u esb-api  /  journalctl -u esb-scraper"
echo ""
echo "Edit $APP_DIR/config.json with your ESB credentials and tariff rates."