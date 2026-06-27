# VFS Global Visa Appointment Bot

Monitors the VFS Global website for available Italy Schengen visa appointment slots in Edinburgh and emails you the moment one appears.

## Quick Start

```bash
cd visa-bot

# 1. Install dependencies (Chromium is pre-installed in this environment)
pip install playwright python-dotenv
playwright install chromium   # only needed once on your local machine

# 2. Configure credentials
cp .env.example .env
# Edit .env with your VFS login, Gmail App Password, etc.

# 3. Run the bot
python bot.py
```

## Configuration (`.env`)

| Variable | Description |
|---|---|
| `VFS_EMAIL` | Your VFS Global account email |
| `VFS_PASSWORD` | Your VFS Global password |
| `VFS_CITY` | `Edinburgh` (default) |
| `VFS_COUNTRY` | `Italy` (default) |
| `VFS_VISA_CATEGORY` | `Schengen Visa` (default) |
| `TARGET_MONTHS` | Months to watch, e.g. `2025-07,2025-08` |
| `POLL_INTERVAL_SECONDS` | How often to check (default: `300` = 5 min) |
| `NOTIFY_EMAIL` | Where to send alerts (e.g. `samsonsoji@gmail.com`) |
| `GMAIL_USER` | Gmail address used to send alerts |
| `GMAIL_APP_PASSWORD` | Gmail [App Password](https://myaccount.google.com/apppasswords) (not your main password) |
| `AUTO_BOOK` | `false` (default) — set `true` to auto-book first slot (experimental) |

### Gmail App Password
You need a Gmail App Password, **not** your normal Gmail password:
1. Go to https://myaccount.google.com/apppasswords
2. Select "Mail" + "Other device", name it "VFS Bot"
3. Copy the 16-character password into `GMAIL_APP_PASSWORD`

## How It Works

1. Launches headless Chromium
2. Logs into your VFS Global account
3. Navigates to Book Appointment → Italy → Edinburgh → Schengen Visa
4. Scans the calendar for available (non-greyed-out) dates in your target months
5. If slots found → prints to terminal + emails you with a direct booking link
6. If no slots → waits `POLL_INTERVAL_SECONDS` and checks again

## Running in Background (Linux/Mac)

```bash
# Keep running after you close the terminal
nohup python bot.py > bot.log 2>&1 &
tail -f bot.log   # watch the log

# Stop it
kill %1
```

## Running as a systemd Service (persistent)

```bash
# /etc/systemd/system/vfs-bot.service
[Unit]
Description=VFS Appointment Bot

[Service]
WorkingDirectory=/path/to/cd-agency/visa-bot
ExecStart=/usr/bin/python3 bot.py
EnvironmentFile=/path/to/cd-agency/visa-bot/.env
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Notes

- **VFS pages change** — if selectors break, open `bot.py` and update the `SEL_*` constants
  at the top. Run with `headless=False` temporarily to see what the page looks like.
- The bot re-logs in on every check to avoid session timeouts
- Slots go **very fast** — keep `POLL_INTERVAL_SECONDS` at 180–300 (3–5 min)
- For September 4 travel, Italy requires applications ≥3 months before travel,
  so July appointments are the right window
