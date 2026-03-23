# Yahoo Fantasy Baseball CLI Bot

A command-line tool for analyzing your Yahoo Fantasy Baseball league and generating AI-powered recommendations. Get actionable insights on streaming pitchers, waiver wire targets, matchup analysis, and roster decisions.

## Features

- **Roster analysis** — View your active roster, bench, and injured list
- **H2H category snapshots** — Track category-by-category scoring vs. opponent
- **Streaming pitcher targets** — Find available pitchers ranked by scoring potential (ERA, WHIP, K9, schedule difficulty)
- **Waiver wire recommendations** — Discover eligible RP targets by saves + holds
- **Recent transactions** — Monitor MLB call-ups, send-downs, and IL moves
- **Schedule density** — See which of your players have the most games coming up
- **AI advisor** — Get actionable advice via Claude or Google Gemini (with real-time Google Search grounding)
- **API caching** — Cache Yahoo Fantasy and MLB Stats API responses for faster dev/debug iterations

## Setup

### 1. Yahoo Fantasy OAuth

1. Create a Yahoo Developer app: https://developer.yahoo.com/apps/create/
   - Select "Fantasy Sports" for API permissions (Read or Read/Write)
   - For "Callback Domain", use `localhost` (local script)
   - Note your **Consumer Key** and **Consumer Secret**

2. Create `oauth2.json` in the project root:
   ```json
   {
     "consumer_key": "YOUR_CONSUMER_KEY",
     "consumer_secret": "YOUR_CONSUMER_SECRET",
     "token": null,
     "token_secret": null
   }
   ```

3. On first run, the bot will prompt you to authorize:
   - Visit the provided URL and log in to Yahoo
   - Authorize the app and copy the verifier code
   - Paste the code when prompted
   - Tokens save automatically for future use

### 2. AI Provider Setup (Optional)

Choose one or both:

**Claude API (Anthropic):**
```bash
export ANTHROPIC_API_KEY="your-api-key"
```

**Google Gemini:**
```bash
export GOOGLE_API_KEY="your-api-key"
```

Both can be set — the bot auto-selects based on which key is available.

### 3. Email Setup (Optional)

Set these environment variables to enable daily email reports:

```bash
export SMTP_SERVER="smtp.gmail.com"      # Gmail or your SMTP server
export SMTP_PORT="587"
export SMTP_USER="your-email@gmail.com"
export SMTP_PASSWORD="your-app-password" # Use Gmail app password, not account password
export EMAIL_TO="recipient@example.com"
```

**For Gmail:**
1. Enable 2-factor authentication on your account
2. Create an [App Password](https://myaccount.google.com/apppasswords)
3. Use the generated 16-character password as `SMTP_PASSWORD`

Then run:
```bash
python fantasy_bot.py email-report
```

Or schedule daily (6 AM):
```bash
# Via crontab
crontab -e
# Add: 0 6 * * * cd ~/fantasy-bot && .venv/bin/python fantasy_bot.py --cache email-report

# Or via systemd (see Deployment section)
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Show roster and all analysis sections
python fantasy_bot.py analyze

# Show only streaming pitchers
python fantasy_bot.py analyze --section streaming

# View specific section: injuries, streaming, waivers, waiver_pitchers, categories, news
python fantasy_bot.py analyze --section <section>

# Use cached API data (faster for dev/debug)
python fantasy_bot.py --cache analyze

# Clear cached Yahoo and MLB Stats API data
python fantasy_bot.py clear-cache

# Get AI-powered advice (requires ANTHROPIC_API_KEY or GOOGLE_API_KEY)
python fantasy_bot.py advise

# Email daily report (requires email env vars)
python fantasy_bot.py email-report

# Email to specific recipient (overrides EMAIL_TO)
python fantasy_bot.py email-report --email alternate@example.com
```

### Available Sections

- `injuries` — Roster alerts (IL, day-to-day, etc.)
- `streaming` — Available pitchers ranked by scoring potential
- `waivers` — Waiver wire hitter targets
- `waiver_pitchers` — Waiver-eligible relief pitchers (FA + waivers) ranked by saves + holds
- `categories` — Head-to-head category snapshots vs. opponent
- `news` — Recent MLB transactions and your roster's upcoming game schedule

## Example Workflows

**Check for streamer pickups:**
```bash
python fantasy_bot.py --cache analyze --section streaming
```

**Monitor waiver targets with recent news:**
```bash
python fantasy_bot.py --cache analyze --section waiver_pitchers news
```

**Get AI recommendations:**
```bash
python fantasy_bot.py advise
```
(Requires Claude or Gemini API key. Uses Google Search with Gemini for real-time MLB news.)

## Requirements

- Python 3.8+
- `yahoo-fantasy-api` — Yahoo Fantasy API wrapper
- `yahoo-oauth` — Yahoo OAuth authentication
- `requests` — HTTP library
- `python-dotenv` — `.env` file support
- `anthropic` — Claude API (optional, for advise command)
- `google-genai` — Google Gemini API (optional, for advise command)

## Configuration

### Caching

The `--cache` flag enables persistent caching of API responses:
- Yahoo Fantasy API responses → `.yahoo_cache.pkl`
- MLB Stats API responses → `.mlb_cache.pkl`

Use this during development to avoid re-fetching data. Clear with:
```bash
python fantasy_bot.py clear-cache
```

### Environment Variables

| Variable            | Purpose                      |
|-------------------|------------------------------|
| `ANTHROPIC_API_KEY` | Claude API key (optional)    |
| `GOOGLE_API_KEY`   | Gemini API key (optional)    |
| `YAHOO_OAUTH_FILE` | Path to `oauth2.json` (default: `./oauth2.json`) |
| `SMTP_SERVER`      | SMTP server address (default: `smtp.gmail.com`) |
| `SMTP_PORT`        | SMTP port (default: `587`)   |
| `SMTP_USER`        | SMTP username for authentication |
| `SMTP_PASSWORD`    | SMTP password or app password |
| `EMAIL_TO`         | Recipient email address      |

## Files

- `fantasy_bot.py` — CLI interface and formatters
- `client.py` — Yahoo Fantasy API client with OAuth
- `analyzer.py` — Core analysis logic (roster, matchups, rankings)
- `mlb_stats.py` — MLB Stats API wrapper (transactions, schedule, stats)

## Extending

To add new analysis sections:
1. Create a function in `analyzer.py` (e.g., `_my_analysis(lg, progress=None)`)
2. Add it to the `runners` dict in `analyze()`
3. Create a formatter in `fantasy_bot.py` (e.g., `_print_my_analysis(data)`)
4. Add to `--section` choices in `main()`

## Resources

- [yahoo-fantasy-api docs](https://yahoo-fantasy-api.readthedocs.io/)
- [MLB Stats API](https://github.com/toddrob99/statsapi)
- [Claude API](https://docs.anthropic.com/)
- [Google Gemini API](https://ai.google.dev/gemini-api/)
