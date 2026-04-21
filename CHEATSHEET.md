# ModelX Operator Cheatsheet

Your running-the-exchange reference. Everything you do day-to-day lives here.

---

## Connecting to the live app

1. In the Railway project canvas, **right-click the `model-x` service card**.
2. Click **"Copy SSH Command"**.
3. Open **Terminal** on your Mac (⌘+Space → "Terminal" → Enter).
4. Paste (⌘+V) and hit Enter.

You'll land at a prompt like `root@abc123:/app#`. You're inside the live container.

**To leave:**

```bash
exit
```

---

## Where things live inside the container

| Path | What it is |
|------|------------|
| `/app/` | Your code — the version from the latest GitHub push |
| `/data/contracts.yaml` | Live market list (edit this) |
| `/data/agents.yaml` | Live agent roster (edit this) |
| `/data/modelx.db` | The database — trades, scores, everything |
| `/data/episode_traces.json` | LLM reasoning traces |

**Rule of thumb:** anything under `/data/` persists across deploys. Anything under `/app/` is wiped on every redeploy.

---

## Add a market

```bash
nano /data/contracts.yaml
```

Copy one of the existing blocks at the bottom, change the fields. Save with **Ctrl+O** then **Enter**. Exit with **Ctrl+X**.

Example block:

```yaml
  - id: gold-jun-2026
    name: "Gold closing price on June 30th 2026"
    description: "Gold closing price on June 30th 2026"
    search_terms:
      - "gold price"
      - "gold futures"
    price_ticker: "GC=F"
    multiplier: 0.001
    position_limit: 100
    max_size: 50
    settlement_date: "2026-06-30 16:00:00T-04:00"
    news_sources:
      - reuters.com
      - bloomberg.com
      - cnbc.com
    max_headlines_per_cycle: 10
```

Indent with **two spaces**. Each entry starts with `- id:`.

**Then restart the service** from Railway: right-click service → **Latest deploy** (or the three-dot menu on the active deployment) → **Restart**.

---

## Stop a market from trading (but keep history)

```bash
nano /data/contracts.yaml
```

Delete the block for that market. Save & exit.

**Restart the service** in Railway.

The market stops advancing immediately, but its data stays in the database and on the dashboard (frozen).

---

## Fully delete a market from the dashboard

First stop it trading (step above), then wipe the database rows:

```bash
sqlite3 /data/modelx.db <<'SQL'
BEGIN;
DELETE FROM phase_traces         WHERE contract_id = 'MARKET_ID';
DELETE FROM fills                WHERE contract_id = 'MARKET_ID';
DELETE FROM orders               WHERE contract_id = 'MARKET_ID';
DELETE FROM quotes               WHERE contract_id = 'MARKET_ID';
DELETE FROM phase_states         WHERE contract_id = 'MARKET_ID';
DELETE FROM accounts             WHERE market_id   = 'MARKET_ID';
DELETE FROM agent_lifetime_stats WHERE market_id   = 'MARKET_ID';
DELETE FROM contracts            WHERE id          = 'MARKET_ID';
DELETE FROM markets              WHERE id          = 'MARKET_ID';
COMMIT;
SQL
```

Replace **both** occurrences of `MARKET_ID` with the actual id (e.g. `cpi-yoy-may-2025`). No restart needed — dashboard refreshes within 2 seconds.

**Tip:** back up the DB before deleting:

```bash
cp /data/modelx.db /data/modelx.db.bak
```

---

## Settle a market

When you know the real-world value:

```bash
python3 /app/settle.py --db /data/modelx.db --market MARKET_ID --value 4201.50
```

Replace `MARKET_ID` and the value. No restart needed. The market moves to `SETTLED` and appears on the dashboard's **Lifetime** tab.

To force-settle a market that isn't yet in `PENDING_SETTLEMENT`:

```bash
python3 /app/settle.py --db /data/modelx.db --market MARKET_ID --value 4201.50 --force
```

---

## Add or remove agents

```bash
nano /data/agents.yaml
```

Each entry:

```yaml
  - name: my-label
    model: anthropic/claude-sonnet-4      # or any OpenRouter model id
    role: MM                              # or HF
```

Save & exit. **Restart the service** in Railway.

---

## Peek at the database

```bash
# Which markets exist, and in what state?
sqlite3 /data/modelx.db "SELECT id, state FROM markets;"

# How many fills has a market had?
sqlite3 /data/modelx.db "SELECT COUNT(*) FROM fills WHERE contract_id = 'sp500-hourly';"

# What's the latest mark on each market?
sqlite3 /data/modelx.db \
  "SELECT contract_id, phase_type, mark FROM phase_states
   WHERE mark IS NOT NULL ORDER BY created_at DESC LIMIT 20;"
```

Interactive shell (type `.exit` to leave):

```bash
sqlite3 /data/modelx.db
```

---

## Back up / restore the database

**Back up** (inside the container):

```bash
cp /data/modelx.db /data/modelx.db.bak-$(date +%Y%m%d-%H%M%S)
```

**Download a backup to your Mac** — in Terminal on your Mac (not inside the container):

```bash
railway ssh "cat /data/modelx.db" > modelx.db.backup
```

**Restore** — upload back and overwrite (dangerous, stop the service first):

1. In Railway, **Restart** or **Stop** the service.
2. `railway ssh` back in.
3. `cp /data/modelx.db.bak-YYYYMMDD-HHMMSS /data/modelx.db`
4. Start/restart the service.

---

## Logs & debugging

**Live logs:** Railway → your service → **Deployments** → active deployment → **View logs**. Scrolls in real time.

**Check what the runner is doing right now:**

```bash
# Inside the container, tail the process output via ps:
ps aux | grep python
```

Anything printed by `run_live.py` goes to Railway's log viewer.

---

## Secrets and config

**Environment variables** (API keys, tuning) are set in Railway, not on disk:

Railway → service → **Variables** tab. Add/edit/delete there. Any change triggers a redeploy.

Required:
- `OPENROUTER_API_KEY` — your OpenRouter key (or comma-separated list of keys for rotation)

Optional overrides (you usually don't need these):
- `DB_PATH` — defaults to `/data/modelx.db`
- `CONTRACT_YAML` — defaults to `/data/contracts.yaml`
- `AGENTS_YAML` — defaults to `/data/agents.yaml`

---

## Deploying code changes

If I update code on your laptop, you push it to GitHub and Railway auto-deploys:

```bash
cd ~/path/to/model-x            # wherever your local repo lives
git status                      # see what changed
git add -A                      # stage all changes (or list specific files)
git commit -m "describe what changed"
git push                        # triggers a Railway redeploy
```

Watch the deploy in Railway → **Deployments** tab. A new entry appears, status flips from Building → Deploying → Active. Takes 3–5 minutes.

If a deploy fails:
- Click the failed deployment → **View logs**
- Screenshot the error and send it to me.

---

## Restart vs Redeploy — which one?

| I just... | Do this |
|-----------|---------|
| Edited `/data/*.yaml` | **Restart** |
| Ran `settle.py` | Nothing — it takes effect immediately |
| Deleted DB rows | Nothing — dashboard refreshes on its own |
| Pushed code to GitHub | Railway auto-deploys; no action needed |
| Changed Variables in Railway | Railway auto-redeploys |
| Something's just broken, unsure why | Try **Restart** first. If that doesn't fix it, **Redeploy** |

**Restart** = relaunch the process on the same image (fast, seconds).
**Redeploy** = rebuild the image from the same GitHub commit (slower, 3–5 min).

---

## Common gotchas

- **YAML indentation must be exactly two spaces.** Tabs break it. If the runner crashes on restart with a YAML error, that's almost always indentation.
- **Editing a market's fields (like `multiplier`) after it's been created does nothing.** The database copy is what the runner uses. To change those fields, delete the market and recreate it under a new `id`.
- **Removing a market from yaml doesn't delete it from the dashboard.** Use the SQL delete block above if you want it gone.
- **Don't run the same market id twice.** Each `id` must be unique across the whole database lifetime (even after settling).
- **The container `/app/` filesystem is read-only in practice.** Never edit yaml at `/app/contracts.yaml` — always use `/data/`.

---

## Escalation

If something's really broken:

1. Railway → **Deployments** → screenshot the latest logs.
2. Send me the screenshot + what you were doing when it broke.

Most problems are yaml typos or a stuck restart. Both are easy to fix.
