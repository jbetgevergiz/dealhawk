# 🦅 DealHawk

**Self-hosted price monitor with Telegram alerts for Amazon and Newegg.**

![Status](https://img.shields.io/badge/status-live%20in%20production-brightgreen)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## What It Does

DealHawk watches a list of products across Amazon and Newegg, stores every price observation in a local SQLite database, and calculates a 30-day rolling average per product. When the current price drops a configurable percentage below that average, it fires a Telegram alert. It runs as a Docker container on a Proxmox LXC — no external services, no subscriptions, no cloud dependency.

The scraper rotates user agents, respects request delays, and falls back through PCPartPicker → Amazon → Newegg per product. An embedded Flask health endpoint exposes uptime and staleness state for external monitors.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Container                          │
│                                                                  │
│  ┌──────────────┐    ┌─────────────────────────────────────┐    │
│  │  config.yml  │───▶│           APScheduler               │    │
│  │  (watchlist) │    │    (interval_hours, default: 1h)    │    │
│  └──────────────┘    └──────────────┬──────────────────────┘    │
│                                     │                            │
│                                     ▼                            │
│                        ┌────────────────────────┐               │
│                        │       Scrapers          │               │
│                        │  PCPartPicker (first)   │               │
│                        │  Amazon (fallback)      │               │
│                        │  Newegg (fallback)      │               │
│                        └──────────┬─────────────┘               │
│                                   │                              │
│                                   ▼                              │
│                        ┌────────────────────────┐               │
│                        │    SQLite Database      │               │
│                        │  data/dealhawk.db       │               │
│                        │  (price history,        │               │
│                        │   alert log, failures)  │               │
│                        └──────────┬─────────────┘               │
│                                   │                              │
│                                   ▼                              │
│                        ┌────────────────────────┐               │
│                        │  Analysis Engine        │               │
│                        │  30-day rolling avg     │               │
│                        │  drop_threshold_pct     │               │
│                        │  cooldown check         │               │
│                        └──────────┬─────────────┘               │
│                                   │                              │
│          ┌────────────────────────┼────────────────────┐        │
│          │                        │                    │         │
│          ▼                        ▼                    ▼         │
│  ┌──────────────┐     ┌──────────────────┐   ┌──────────────┐  │
│  │   No alert   │     │  Telegram alert  │   │  /health     │  │
│  │   (price OK) │     │  (price dropped) │   │  :8085       │  │
│  └──────────────┘     └────────┬─────────┘   └──────────────┘  │
│                                 │                                │
└─────────────────────────────────┼────────────────────────────────┘
                                  │
                     ┌────────────▼────────────┐
                     │   Cloudflare Tunnel      │
                     │  (optional: expose       │
                     │   /health externally     │
                     │   without open ports)    │
                     └─────────────────────────┘
```

**Hosting context:** Runs in an unprivileged Proxmox LXC (Debian 12, 1 vCPU, 512MB RAM). The `data/` volume is bind-mounted on the LXC host filesystem for persistence across container rebuilds. Cloudflare Tunnel is used to expose the `/health` endpoint to an external uptime monitor (UptimeRobot) without punching holes in the firewall.

---

## Tech Decisions

- **APScheduler over host cron** — The scheduler runs inside the container, which means the job schedule travels with the deployment. No host-level cron entries to manage, no `crontab -e` on the LXC, and the interval is just a config value. Easier to change, easier to reason about.

- **SQLite over Postgres** — This is a single-writer, single-reader workload. SQLite is zero-ops, the DB file lives in the mounted volume, and it survives container rebuilds without a separate database service. At the scale of tracking 10-20 products, Postgres would be infrastructure theater.

- **Flask for health endpoint (not FastAPI/external service)** — The health check needs to answer "is the scheduler still running and are my scrapes succeeding?" Flask adds minimal overhead, runs in the same process, and has direct access to scheduler state and DB queries. No IPC, no separate service.

- **Config-file driven over environment variables** — The watchlist can have 20+ products with per-product overrides. Stuffing that into env vars would be a maintenance nightmare. `config.yml` gives you comments, structure, and easy diffs when you add or remove products.

- **curl_cffi for scraping** — Regular `requests` with standard TLS fingerprints gets blocked by Amazon's bot detection within a few hours. `curl_cffi` mimics a real browser's TLS fingerprint, which significantly reduces block rate without needing Playwright or a headless browser.

---

## Setup

### Prerequisites

- Docker (or Docker Compose)
- A Telegram bot token ([get one from BotFather](https://t.me/BotFather))
- Your Telegram chat ID

### Deploy with Docker Compose (recommended)

```bash
git clone https://github.com/jbetgevergiz/dealhawk.git
cd dealhawk
cp config.yml.example config.yml
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=7123456789:AAF...
TELEGRAM_CHAT_ID=-1001234567890
```

Edit `config.yml` with your watchlist (see Configuration section below).

```yaml
# docker-compose.yml
services:
  dealhawk:
    image: ghcr.io/jbetgevergiz/dealhawk:latest
    container_name: dealhawk
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./config.yml:/app/config.yml:ro
      - ./data:/app/data
    ports:
      - "8085:8085"
```

```bash
docker compose up -d
docker compose logs -f  # confirm first scrape cycle runs clean
```

The `data/` directory is created automatically on first run. It contains the SQLite database — don't delete it.

### Run Locally (without Docker)

```bash
pip install -r requirements.txt
cp config.yml.example config.yml && cp .env.example .env
# Edit both files
export $(cat .env | xargs)
python -m src.main
```

---

## Configuration

```yaml
alerts:
  default_drop_threshold_pct: 15   # Alert when price is ≥15% below rolling avg
  default_lookback_days: 30        # Rolling avg window
  alert_cooldown_hours: 24         # Don't re-alert same product within 24h
  failure_alert_threshold: 3       # Scrape failures before alerting on the failure itself

scraping:
  interval_hours: 1                # Scrape cycle frequency
  request_timeout_seconds: 30
  request_delay_seconds: 3         # Polite delay between requests
  user_agents:                     # Rotated per request
    - "Mozilla/5.0 (Windows NT 10.0; Win64; x64)..."

freshness:
  staleness_ttl_hours: 6           # Mark component stale after N hours without success
  no_data_threshold_hours: 24      # Health endpoint flags "no data" at this threshold

components:
  - name: "27-inch 1440p Gaming Monitor"   # Label in Telegram alerts
    category: "monitor"                    # PCPartPicker category (optional)
    search_terms:
      - "27 inch 1440p 144hz gaming monitor"
      - "27 inch QHD IPS gaming monitor"
    retailers: [amazon, newegg]            # Tried in order: pcpartpicker → amazon → newegg
    drop_threshold_pct: 20                 # Per-product override
    lookback_days: 30
```

**Required fields per component:** `name`, `search_terms`, `retailers`
**Optional overrides:** `drop_threshold_pct`, `lookback_days`, `category`

Minimum 5 price observations before alerts fire — this prevents false positives on initial data.

---

## Known Failure Modes & Recovery

These are failure modes I've actually hit running this in production.

### 1. Scraper blocked by Amazon

**Symptom:** Consecutive scrape failures for Amazon products. Health endpoint returns `stale_components > 0`. Telegram alert fires after `failure_alert_threshold` failures.

**What happens:** Amazon's bot detection rotates its fingerprinting heuristics. A user agent that worked yesterday gets flagged today.

**Recovery:**
- Update the `user_agents` list in `config.yml` with fresh browser UA strings (pull from https://www.whatismybrowser.com/guides/the-latest-user-agents/)
- Increase `request_delay_seconds` to 5–10 to appear more human
- Add PCPartPicker as a retailer for electronics — it often has Amazon pricing without the scraping friction
- If it persists: Newegg is generally more scraper-tolerant and usually has comparable pricing for PC hardware

### 2. Telegram rate limit / message flood

**Symptom:** Alerts stop delivering. Telegram API returns HTTP 429.

**What causes it:** If you add 15+ products and they all have price drops in the same scrape cycle (e.g., Prime Day), the bot can hit Telegram's 30 messages/second or 20 messages/minute-per-chat limits.

**Recovery:**
- DealHawk has a built-in `alert_cooldown_hours` that prevents re-alerting, but doesn't batch. If you're tracking many items, increase `alert_cooldown_hours` to reduce fire rate.
- If you hit a 429, Telegram will return a `retry_after` value in the response. The alerter logs this — check `docker compose logs dealhawk | grep 429`.
- As a workaround: split products across two separate bot instances with different chat IDs (two `config.yml` files, two containers).

### 3. SQLite database corruption

**Symptom:** Container crashes on startup with `DatabaseError: database disk image is malformed` or the health endpoint returns 503.

**What causes it:** Abrupt container kill during a write (power loss, OOM kill, forced `docker stop`). SQLite WAL mode reduces but doesn't eliminate this risk.

**Recovery:**
```bash
# Stop the container
docker compose down

# Attempt SQLite integrity check
sqlite3 data/dealhawk.db "PRAGMA integrity_check;"

# If corrupt, recover what you can:
sqlite3 data/dealhawk.db ".recover" | sqlite3 data/dealhawk_recovered.db
mv data/dealhawk.db data/dealhawk.db.bak
mv data/dealhawk_recovered.db data/dealhawk.db

# Restart
docker compose up -d
```

**Prevention:** Enable SQLite WAL mode (already set in `db.py`). Take periodic backups of `data/dealhawk.db` — it's small enough to copy daily via a cron job on the LXC host:
```bash
# /etc/cron.daily/dealhawk-backup
cp /opt/dealhawk/data/dealhawk.db /opt/dealhawk/backups/dealhawk-$(date +%F).db
find /opt/dealhawk/backups -mtime +7 -delete
```

### 4. Scheduler silently stops running

**Symptom:** No Telegram alerts, no scrape failures, health endpoint shows old `last_scrape_run` timestamp.

**What causes it:** APScheduler can occasionally deadlock or fail to reschedule a job if a scrape task throws an unhandled exception. The process stays alive but the scheduler is dead.

**Recovery:**
- The `/health` endpoint checks `last_scrape_run` against `staleness_ttl_hours`. If staleness is detected, it returns HTTP 503, which triggers UptimeRobot.
- Fix: `docker compose restart dealhawk`. Job scheduling resumes immediately on restart.
- Long-term: set a container restart policy (`restart: unless-stopped`) and consider a watchdog that curls `/health` from the LXC host and restarts if it gets a non-200.

---

## Operational Notes

Running in production since late 2024 on a Proxmox 8 home lab:
- **Host:** Proxmox LXC (Debian 12), 1 vCPU / 512MB RAM — it's idle 95% of the time
- **Database:** ~2MB after 90 days of tracking 8 products hourly
- **Uptime:** Monitored via UptimeRobot hitting `/health` through Cloudflare Tunnel every 5 minutes
- **Typical alert latency:** 1–5 minutes after a price drop is published (depends on when the hourly scrape lands)
- **False positive rate:** Low after the 5-datapoint minimum. The 30-day window smooths out temporary price spikes that would otherwise inflate the baseline.
- **Scrape success rate:** ~85–90% on Amazon (varies by product category and time of day), ~95%+ on Newegg

The Cloudflare Tunnel setup means zero exposed ports on the LXC host. The tunnel authenticates with a Cloudflare-issued certificate and routes `/health` to `localhost:8085` inside the container.

---

## Project Structure

```
dealhawk/
├── src/
│   ├── main.py             # Entry point: logging, DB init, scheduler start, Flask
│   ├── config.py           # config.yml loader and validation
│   ├── db.py               # SQLite schema, price insertion, history queries
│   ├── scheduler.py        # APScheduler job: scrape → analyze → alert
│   ├── analysis.py         # Rolling average, drop detection, cooldown logic
│   ├── alerter.py          # Telegram message formatting and delivery
│   ├── healthcheck.py      # Flask /health endpoint
│   └── scrapers/
│       ├── base.py         # ScrapeResult / ScrapeOutcome dataclasses
│       ├── amazon.py       # Amazon search scraper
│       ├── newegg.py       # Newegg search scraper
│       └── pcpartpicker.py # PCPartPicker scraper
├── config.yml.example      # Annotated config template
├── .env.example            # Env var template
├── requirements.txt
└── README.md
```

---

## How Deal Detection Works

1. Every hour (configurable), the scheduler triggers a scrape of all products in `config.yml`
2. Each price observation is stored in SQLite with a timestamp
3. After 5+ observations exist for a product, the analysis engine calculates a rolling average of the lowest price seen per scrape session over the last 30 days
4. If `current_price <= rolling_avg * (1 - drop_threshold_pct / 100)`, a deal is flagged
5. The alerter checks cooldown state, then fires a Telegram message with product name, current price, rolling average, and % drop
6. Alert state is written back to SQLite to enforce the cooldown on next cycle

---

## Future Improvements

- **Price history charts in alerts** — Generate a small matplotlib chart of 30-day price history and attach it to the Telegram message. Right now alerts are text-only; a chart would make the "is this actually a deal" judgment instant.
- **Walmart / Best Buy scrapers** — Newegg and Amazon miss a lot of appliances and TVs where Walmart and Best Buy are more competitive. The scraper base class makes adding new retailers straightforward.
- **Webhook for Home Assistant** — Fire a webhook event so deals can trigger automations (TTS announcement, lights, etc.) in addition to Telegram. Already have the Flask server running; this is a small addition.
- **Per-product price floor** — Sometimes you know you won't buy something above $X regardless of what the rolling average says. A `max_acceptable_price` field per component would prevent alerts on "drops" that are still overpriced.
- **Config hot-reload** — Right now, adding a product to `config.yml` requires a container restart. APScheduler supports job replacement at runtime; wiring that to a file watcher would make watchlist edits instant.

---

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Set `TELEGRAM_BOT_TOKEN` in `.env`
3. Start a chat with your bot, then fetch:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Send your bot any message, look for `"chat": {"id": ...}` in the response
5. Set `TELEGRAM_CHAT_ID` in `.env`

For a group chat or channel: add the bot as admin and use the negative chat ID.

---

## Health Endpoint

```bash
curl http://localhost:8085/health
```

```json
{
  "status": "healthy",
  "uptime_hours": 312.4,
  "last_scrape_run": "2025-03-15T09:00:01",
  "components_tracked": 8,
  "stale_components": 0,
  "consecutive_failures": {}
}
```

Returns HTTP 200 when healthy, HTTP 503 when degraded (stale components or scheduler failure). Wire this into UptimeRobot, Grafana, or whatever you're using for uptime monitoring.

---

## License

MIT. Do whatever you want with it.
