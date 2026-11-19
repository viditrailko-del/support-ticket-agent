# Deploying the public demo (free: Render + Neon + Gemini free tier)

Everything in the repo is ready (`render.yaml`, `docker/demo-start.sh`, `.github/workflows/demo-reset.yml`).
What's left needs your own accounts. About 20 minutes, most of it waiting for the first build.

## 0. Push `main` to GitHub
Render deploys from the GitHub repo, so the demo code has to be there first.

## 1. A Gemini key just for the demo
1. Open Google AI Studio → **Get API key** → **Create API key** → choose **Create a new project** (call it
   something like `support-demo`).
2. Why a new project: free-tier quota is per project. The demo then has its own quota, so visitors can never use
   up the quota you need for eval runs, and vice versa.
3. Check that **billing is OFF** on that project (AI Studio shows "Free tier"). With billing off, the worst a
   visitor can do is use up the day's free quota; it can never cost money.

## 2. Neon (the database)
1. Sign up at neon.tech → **Create project**. Only Postgres (no Auth / Data API), **Postgres 16** (what the
   tests and CI run), region **AWS US East (N. Virginia)**: `render.yaml` puts the app in Render's `virginia`
   region, so the app and the database sit close together (every ticket makes a few dozen small queries).
2. On the project dashboard → **Connect** → turn **Connection pooling OFF** → copy the connection string
   (`postgresql://...neon.tech/neondb?sslmode=require...`).
   Use the direct one, not the pooled one: our driver (psycopg 3) uses prepared statements, which the pooler
   can break. The app converts `postgresql://` to its own driver by itself, so paste it as it is.

## 3. Render (the app)
1. Sign up at render.com with your GitHub account.
2. **New → Blueprint** → pick the `support-ticket-agent` repo, branch `main`, blueprint path `render.yaml`
   (the default). Render reads it and shows one free web service, `support-ticket-agent-demo`, in Virginia.
3. It asks for the two secret values: `DATABASE_URL` = the Neon string, `GEMINI_API_KEY` = the demo key.
   `DEMO_MODE` and `DEMO_RESET_TOKEN` fill themselves in.
4. **Apply**. The first build takes ~5-10 minutes. When the service shows **Live**, open its URL
   (`https://support-ticket-agent-demo.onrender.com` or similar). The first start runs the migrations and loads
   the shop into Neon by itself.

## 4. The nightly reset (GitHub Actions)
1. Render → the service → **Environment** → copy the value of `DEMO_RESET_TOKEN`.
2. GitHub repo → **Settings → Secrets and variables → Actions**:
   - **Secrets** tab → New secret `DEMO_RESET_TOKEN` = that value.
   - **Variables** tab → New variable `DEMO_URL` = the Render URL, without a trailing slash.
3. **Actions** tab → **demo-reset** → **Run workflow** once, to check it goes green.

## 5. Send me the URL
I'll check it from here: every recorded scenario, the page, the live quota, the reset.

## Good to know
- **Free instances sleep** after about 15 minutes with no visitors. The next visit wakes it in about a minute
  (the browser just keeps loading). Open the link yourself a minute before you share it on a call.
- **Every push to `main` redeploys** (`autoDeploy: true`). CI checks the same boot on a fresh database first.
- **Live mode caps**: 30 live tickets a day for everyone, and 5 per visitor per hour. Change them with the
  `DEMO_LIVE_DAILY_LIMIT` / `DEMO_LIVE_PER_IP_PER_HOUR` environment variables on Render. The per-visitor cap
  trusts the `X-Forwarded-For` header, which a determined visitor can fake; the daily cap is the one that
  protects the quota.
- **Scheduled workflows pause** after 60 days without commits to the repo. GitHub emails you; re-enable it in
  the Actions tab.
