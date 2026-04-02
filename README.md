# 🦅 DealHawk

**Self-hosted price monitor and Telegram alert bot for Amazon & Newegg.**

Track any product — electronics, appliances, gaming gear, home goods — and get notified automatically when the price drops significantly below its rolling average.

---

## What It Does

- **Scrapes Amazon & Newegg** on a configurable schedule (default: hourly)
- **Calculates a 30-day rolling average** for each product you're tracking
- **Sends a Telegram alert** when the current price drops ≥X% below the average
- **Falls back gracefully**: tries PCPartPicker → Amazon → Newegg for each product
- **Health endpoint** at `:8085/health` for uptime monitoring
- **Scrape failure alerts** when a product goes dark for too many consecutive cycles
- **Alert cooldown** to prevent duplicate notifications

---

## Use Cases

- Gaming monitors, keyboards, headsets, speakers
- Coffee makers, air fryers, blenders, appliances
- Laptops, tablets, hard drives, SSDs
- Anything sold on Amazon or Newegg

---

## Quick Start (Docker)

### 1. Clone and configure

```bash
git clone https://github.com/jbetgevergiz/dealhawk.git
cd dealhawk
cp config.yml.example config.yml
cp .env.example .env
```

### 2. Edit your watchlist

Open `config.yml` and add the products you want to track:

```yaml
components:
  - name: "27-inch 1440p Gaming Monitor"
    category: "monitor"
    search_terms:
      - "27 inch 1440p 144hz gaming monitor"
    retailers: [amazon, newegg]
    drop_threshold_pct: 20
```

### 3. Add your Telegram credentials

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 4. Run with Docker

```bash
docker run -d \
  --name dealhawk \
  --restart unless-stopped \
  -v $(pwd)/config.yml:/app/config.yml:ro \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/data:/app/data \
  -p 8085:8085 \
  --env-file .env \
  ghcr.io/jbetgevergiz/dealhawk:latest
```

Or with Docker Compose:

```yaml
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
```

---

## Running Locally (Python)

```bash
pip install -r requirements.txt
cp config.yml.example config.yml
cp .env.example .env
# Edit config.yml and .env

export $(cat .env | xargs)
python -m src.main
```

---

## Configuration Reference

```yaml
alerts:
  default_drop_threshold_pct: 15   # Alert when price drops this % below average
  default_lookback_days: 30        # Days of history for rolling average
  alert_cooldown_hours: 24         # Hours between repeat alerts for same product
  failure_alert_threshold: 3       # Consecutive failures before sending alert

scraping:
  interval_hours: 1                # Scrape every N hours
  request_timeout_seconds: 30      # Per-request timeout
  request_delay_seconds: 3         # Delay between search terms (polite scraping)
  user_agents: [...]               # Rotate to reduce blocking

freshness:
  staleness_ttl_hours: 6           # Mark data stale after N hours with no success
  no_data_threshold_hours: 24      # Threshold for "no data" warning

components:
  - name: "Your Product Name"      # Label shown in alerts
    category: "monitor"            # PCPartPicker category (optional)
    search_terms:                  # Tried in order; first match wins
      - "search term one"
      - "search term two"
    retailers: [amazon, newegg]    # Which stores to scrape
    drop_threshold_pct: 20         # Override global threshold (optional)
    lookback_days: 30              # Override lookback window (optional)
```

---

## Telegram Bot Setup

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** → set as `TELEGRAM_BOT_TOKEN` in `.env`
4. Start a chat with your bot, then visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
5. Send your bot a message and look for `"chat": {"id": ...}` in the response
6. Copy that ID → set as `TELEGRAM_CHAT_ID` in `.env`

---

## Health Check

DealHawk exposes a health endpoint at `http://localhost:8085/health`:

```json
{
  "status": "healthy",
  "uptime_hours": 24.5,
  "last_scrape_run": "2024-01-15T14:30:00",
  "components_tracked": 4,
  "stale_components": 0,
  "consecutive_failures": {}
}
```

Returns HTTP 200 when healthy, 503 when degraded.

---

## Project Structure

```
dealhawk/
├── src/
│   ├── main.py           # Entry point: setup logging, DB, scheduler, Flask
│   ├── config.py         # Load and validate config.yml
│   ├── db.py             # SQLite schema, queries, price insertion
│   ├── scheduler.py      # APScheduler job: scrape → analyze → alert
│   ├── analysis.py       # Rolling average calculation and deal detection
│   ├── alerter.py        # Telegram message formatting and sending
│   ├── healthcheck.py    # Flask /health endpoint
│   └── scrapers/
│       ├── base.py       # ScrapeResult / ScrapeOutcome dataclasses
│       ├── amazon.py     # Amazon search scraper
│       ├── newegg.py     # Newegg search scraper
│       └── pcpartpicker.py  # PCPartPicker search scraper
├── config.yml.example    # Annotated config template
├── .env.example          # Environment variable template
├── requirements.txt      # Python dependencies
└── README.md
```

---

## How Deal Detection Works

1. Every hour (configurable), DealHawk scrapes each product from your watchlist
2. Prices are stored in a local SQLite database (`data/dealhawk.db`)
3. After 5+ data points, it calculates a **rolling average** of the lowest price seen per scrape session over the past 30 days
4. If the current price is ≥X% below the rolling average, a **Telegram alert** fires
5. A cooldown (default: 24h) prevents the same product from alerting repeatedly

---

## License

MIT — do whatever you want with it.
