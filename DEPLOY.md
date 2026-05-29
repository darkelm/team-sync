# Deploying SyncBot to the Cloud

SyncBot uses Slack **Socket Mode**, which means it connects *outbound* to Slack over a websocket. It needs **no public URL, no inbound ports, no domain** — so it deploys as a simple always-on background worker.

These instructions use **Railway** (easiest GitHub-integrated worker host). The same files (`Procfile`, `requirements.txt`, `runtime.txt`) also work on Render, Fly.io, or Heroku.

---

## Railway (recommended)

### 1. Sign up
Go to [railway.app](https://railway.app) → **Login with GitHub**. Free trial credit covers a POC easily; the Hobby plan is ~$5/mo.

### 2. Create the project from your repo
- Click **New Project** → **Deploy from GitHub repo**
- Authorize Railway to access your GitHub
- Select **`darkelm/team-sync`**
- Railway auto-detects Python via `requirements.txt` and uses the start command in `railway.json` (`python slack_bot.py`)

### 3. Set environment variables
In the Railway project → **Variables** tab → add these (copy values from your local `.env`):

```
JIRA_PROVIDER=live
CONFLUENCE_PROVIDER=live
GITHUB_PROVIDER=local
SLACK_PROVIDER=live
FIGMA_PROVIDER=local

ATLASSIAN_URL=https://tyshawdesign.atlassian.net
ATLASSIAN_EMAIL=tyshawdesign@gmail.com
ATLASSIAN_API_TOKEN=<your rotated token>

SLACK_BOT_TOKEN=<xoxb-...>
SLACK_APP_TOKEN=<xapp-...>
SLACK_SIGNING_SECRET=<...>

ANTHROPIC_API_KEY=<optional, for the chat agent>
```

> ⚠️ Use **freshly rotated tokens** here — never the ones shared in chat/dev.

### 4. Deploy
Railway builds and starts automatically. Watch the **Deploy Logs** — you should see:
```
SyncBot starting (Socket Mode)...
Providers: Jira=live | Confluence=live | Slack=live
⚡️ Bolt app is running!
```

### 5. Test
In Slack: `@syncbot scan for conflicts` — it now responds 24/7, independent of your laptop.

### 6. Auto-deploy on push
Railway redeploys automatically every time you push to `main`. No extra setup.

---

## Render (alternative)

Render's free tier only runs **web services** (which sleep), not background workers — workers require a paid plan (~$7/mo). If you use Render:
- New → **Background Worker** → connect repo
- Build command: `pip install -r requirements.txt`
- Start command: `python slack_bot.py`
- Add the same environment variables

## Fly.io (alternative)

```bash
fly launch --no-deploy        # generates fly.toml
fly secrets set SLACK_BOT_TOKEN=... SLACK_APP_TOKEN=... ATLASSIAN_API_TOKEN=... ...
fly deploy
```
Set `[processes] app = "python slack_bot.py"` in `fly.toml` and remove any HTTP service/port section (Socket Mode needs no inbound port).

---

## Notes

- **Synthetic data ships with the repo** (`data/synthetic/`), so GitHub/Figma providers work in `local` mode on the cloud with no extra setup. Flip them to `live` later via env vars.
- **The bot is stateless** — it reads from providers on each request. Safe to restart anytime.
- **Cost control:** a Socket Mode worker idles cheaply; it only does work when someone messages it or a scheduled digest runs.
