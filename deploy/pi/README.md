# Raspberry Pi Deployment

This guide runs the backend API, SQLite data, model artifacts, and Discord bot on a Raspberry Pi. Cloudflare Pages serves the static frontend, and Cloudflare Tunnel exposes the Pi API over HTTPS without router port forwarding.

## Target Layout

- Repo checkout: `/home/pi/stock-assistant-PiPi`
- Python venv: `/home/pi/stock-assistant-PiPi/.venv`
- Persistent account data: `/home/pi/.local/share/stock-assistant`
- Replaceable model/research data: `/home/pi/stock-assistant-PiPi/data`
- Local API URL: `http://127.0.0.1:8000`
- Public API URL: `https://api.your-domain.com`

If your Pi username or checkout path differs, edit both service files before installing them.

## 1. Prepare The Pi

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl
git clone https://github.com/talitarc777-hash/stock-assistant-PiPi.git /home/pi/stock-assistant-PiPi
cd /home/pi/stock-assistant-PiPi
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data reports
cp .env.example .env
```

Edit `.env`:

```bash
nano .env
```

Recommended Pi values:

```env
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8000
PERSISTENT_DATA_DIR=/home/pi/.local/share/stock-assistant
PROFILE_DB_PATH=data/user_profiles.db
RESEARCH_DATA_DIR=data/research
RESEARCH_MODELS_DIR=data/models
BACKEND_BASE_URL=http://127.0.0.1:8000
CORS_ALLOW_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,https://cowbox.dpdns.org
CORS_ALLOW_ORIGIN_REGEX=^https://([a-z0-9-]+\.)?stock-assistant-pipi\.pages\.dev$
```

Add your real `DISCORD_BOT_TOKEN` and any `ALLOWED_CHANNEL_IDS` in `.env`. Do not commit `.env`.

In production, a relative `PROFILE_DB_PATH` is resolved below
`PERSISTENT_DATA_DIR`. On the first startup after this change, the app copies
the existing `data/user_profiles.db` into the persistent directory when the
new database does not exist. Later Git pulls and application deployments do
not replace that persistent copy.

## 2. Smoke Test Manually

Start the API:

```bash
cd /home/pi/stock-assistant-PiPi
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second terminal:

```bash
curl http://127.0.0.1:8000/health
curl "http://127.0.0.1:8000/analyze?ticker=VOO"
curl "http://127.0.0.1:8000/watchlist-analyze?tickers=VOO,QQQ&period=5y"
```

Stop the API with `Ctrl+C`, then test the bot after the API is running again:

```bash
.venv/bin/python -m bot.main
```

Try Discord commands:

```text
!settings
!analyze VOO
!watchlist
!traderstatus
```

## 3. Install systemd Services

```bash
cd /home/pi/stock-assistant-PiPi
sudo cp deploy/pi/stock-assistant-api.service /etc/systemd/system/
sudo cp deploy/pi/stock-assistant-discord.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable stock-assistant-api stock-assistant-discord
sudo systemctl start stock-assistant-api
sudo systemctl start stock-assistant-discord
```

Check status and logs:

```bash
systemctl status stock-assistant-api --no-pager
systemctl status stock-assistant-discord --no-pager
journalctl -u stock-assistant-api -f
journalctl -u stock-assistant-discord -f
```

## 4. Cloudflare Tunnel

Install `cloudflared` using Cloudflare's Raspberry Pi instructions, then authenticate:

```bash
cloudflared tunnel login
cloudflared tunnel create stock-assistant-api
cloudflared tunnel route dns stock-assistant-api api.your-domain.com
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: stock-assistant-api
credentials-file: /home/pi/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: api.your-domain.com
    service: http://localhost:8000
  - service: http_status:404
```

Install and start the tunnel service:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl restart cloudflared
curl https://api.your-domain.com/health
```

## 5. Cloudflare Pages Frontend

Connect the GitHub repo to Cloudflare Pages:

- Root directory: `frontend`
- Build command: `npm run build`
- Build output directory: `dist`
- Production environment variable: `VITE_API_BASE_URL=https://cowbox.dpdns.org`

After deployment, add your custom frontend domain to `CORS_ALLOW_ORIGINS` in the Pi `.env`, then restart the API:

```bash
sudo systemctl restart stock-assistant-api
```

## 6. Data Migration

Copy existing runtime data into the Pi checkout before retiring Railway or the old host:

```bash
rsync -av data/ pi@<pi-host>:/home/pi/stock-assistant-PiPi/data/
```

Important paths:

- `/home/pi/.local/share/stock-assistant/user_profiles.db`
- `data/research/`
- `data/models/`
- `data/discord_user_settings.json`

Then restart services:

```bash
sudo systemctl restart stock-assistant-api stock-assistant-discord
```

Confirm the web dashboard and Discord bot see the same profile/watchlist data.

## 7. Backups

Create a daily backup script on the Pi, for example `/home/pi/backup-stock-assistant.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/home/pi/stock-assistant-PiPi
PERSISTENT_DIR=/home/pi/.local/share/stock-assistant
BACKUP_DIR=/home/pi/stock-assistant-backups
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/data-$STAMP.tar.gz" \
  -C "$PERSISTENT_DIR" user_profiles.db \
  -C "$APP_DIR" data/research data/models data/discord_user_settings.json
find "$BACKUP_DIR" -name 'data-*.tar.gz' -mtime +14 -delete
```

Enable it with cron:

```bash
chmod +x /home/pi/backup-stock-assistant.sh
crontab -e
```

Add:

```cron
15 3 * * * /home/pi/backup-stock-assistant.sh
```

## 8. Recovery Checklist

After a reboot:

```bash
systemctl status stock-assistant-api --no-pager
systemctl status stock-assistant-discord --no-pager
systemctl status cloudflared --no-pager
curl https://api.your-domain.com/health
```

If the frontend loads but API calls fail, check:

- `VITE_API_BASE_URL` in Cloudflare Pages
- `CORS_ALLOW_ORIGINS` and `CORS_ALLOW_ORIGIN_REGEX` in Pi `.env`
- Cloudflare Tunnel status
- API logs with `journalctl -u stock-assistant-api -f`
