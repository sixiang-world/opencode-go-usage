# opencode-go-usage

A CLI tool for querying OpenCode Go plan usage. It scrapes OpenCode page data to report usage percentage, reset countdowns, and cost breakdowns.

[中文版本](./README.md)

## Features

- **Usage query** — rolling (5-hour), weekly, and monthly usage percentages with reset times
- **Recent requests** — view recent API request records (model, tokens, cost, time)
- **Cost aggregation** — monthly daily cost and model usage distribution
- **Query history** — all queries are automatically saved for later review
- **JSON output** — `--json` flag for script and cron integration
- **Historical months** — `costs --month YYYY-MM` to inspect past months

## Setup

Requires Python 3.11+, `httpx`, and `rich`. Runs via `uv` — no manual installation needed:

```bash
git clone https://github.com/your-org/opencode-go-usage.git
cd opencode-go-usage
```

## Configuration

Three configuration sources are supported (priority order, highest first):

1. **Environment variables**
   ```bash
   export OPENCODE_GO_WORKSPACE_ID='wrk_xxxxx'
   export OPENCODE_GO_AUTH_COOKIE='Fe26.2***'
   ```

2. **Config file** (`~/.opencode-go-usage.json`)
   ```bash
   uv run --with-requirements requirements.txt scripts/opencode-go-usage.py save wrk_xxxxx 'Fe26.2***'
   ```

3. **Cookie file** (`~/opencode-usage/.opencode-auth`) — plain-text compatibility path

### Getting the Cookie

1. Open https://opencode.ai in your browser and log in
2. F12 → Application → Cookies → opencode.ai
3. Copy the `auth` cookie value (starts with `Fe26.2**`, valid for ~1 year)

## Usage

```bash
# Usage query (rich terminal output)
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py

# JSON output (for scripting)
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py --json

# View recent API request records
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py recent

# Recent requests as JSON
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py recent --json

# Monthly cost aggregation for current month
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs

# Cost aggregation for a specific month
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs --month 2026-06

# Cost aggregation as JSON
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs --json

# View query history (last 20 entries)
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py history

# View last 50 history entries
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py history 50
```

### Cron Integration Example

```bash
# Alert if usage exceeds 90%
opencode-go-usage --json | python3 -c "import sys,json;d=json.load(sys.stdin);exit(1 if d.get('rolling',{}).get('used_pct',0)>90 else 0)"
```

## Sample Output

```json
{
  "ok": true,
  "timestamp": "2026-06-23T09:00:19+08:00",
  "account": "user@example.com",
  "rolling": {
    "used_pct": 2.1,
    "resets_in": 7261,
    "resets_at": "2026-06-23T11:01:00+08:00"
  },
  "weekly": {
    "used_pct": 24.3,
    "resets_in": 399600,
    "resets_at": "2026-06-29T00:00:00+08:00"
  },
  "monthly": {
    "used_pct": 63.0,
    "resets_in": 1342800,
    "resets_at": "2026-07-10T08:00:00+08:00"
  }
}
```

## How It Works

1. Fetches `https://opencode.ai/workspace/{id}/usage` using the auth cookie
2. Extracts the `$R[...]=` array from the SolidJS SSR script (containing per-request inputTokens, outputTokens, cost, and timeCreated)
3. Aggregates cost across time windows (rolling 5h / weekly / monthly) and computes quota percentages
4. Outputs rich progress bars or JSON

> **Note:** The Go dashboard page (`/go`) no longer provides quota data directly (`rollingUsage` / `weeklyUsage` / `monthlyUsage` are all null). Usage is now computed from per-request records on the `/usage` page.

## Project Structure

```
├── README.md                  # 中文文档
├── README.en.md               # This file
├── AGENTS.md                  # Contributor guide
├── SKILL.md                   # Codex skill definition
├── requirements.txt           # Python dependencies
├── scripts/
│   └── opencode-go-usage.py   # CLI entry point
└── references/
    └── usage-page-ssr-format.md  # SSR data format reference
```

## Security

- Auth cookie file is automatically set to `chmod 600` after save
- Query history never stores the raw cookie
- `workspace_id` is masked by default in output
