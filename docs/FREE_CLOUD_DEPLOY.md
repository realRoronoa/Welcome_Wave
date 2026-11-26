# Free Cloud Deployment: Neon + Render + Vercel

Deploy Welcome Wave without Google Cloud billing using three free tiers:
- **Neon** — PostgreSQL 16+ with `pgvector` (no 30-day expiry)
- **Render** — Docker web service for the FastAPI backend + in-process background workers ([render.yaml](../render.yaml))
- **Vercel** — Vite/React frontend + `/api/*` and `/webhooks/*` proxy ([frontend/vercel.json](../frontend/vercel.json))

> **Free-tier limits to know upfront:**
> - Render Free web services sleep after 15 minutes of inactivity (the first request takes ~30–50s to wake) and do not persist local disk across restarts (uploaded recordings/documents reset on redeploy).
> - Vercel Hobby rewrites proxy `/api/*` cleanly, though long-lived `/api/live` SSE connections time out and automatically reconnect via `frontend/src/live.ts`.

---

## 1. Database on Neon (Free Tier)

1. Create a free project at [neon.tech](https://neon.tech) (PostgreSQL 16 or 17).
2. Copy the **direct** (non-pooled) connection string for the owner role, e.g.:
   `postgresql://neondb_owner:<OWNER_PASS>@ep-xxxx.aws.neon.tech/neondb?sslmode=require`
3. Choose a strong password for the least-privilege `ww_app` database role (`APP_DB_PASSWORD`).
   - `DATABASE_URL_OWNER`: the Neon direct owner URL above (used on boot by `python -m app.migrate` to run migrations, enable `vector` + `pgcrypto`, and create/update `ww_app`).
   - `DATABASE_URL`: the same Neon host and database using `ww_app` and `postgresql+psycopg://`, e.g.:
     `postgresql+psycopg://ww_app:<APP_DB_PASSWORD>@ep-xxxx.aws.neon.tech/neondb?sslmode=require`

---

## 2. Backend on Render (Free Web Service)

1. Go to [dashboard.render.com](https://dashboard.render.com) → **New + → Blueprint**, and connect your GitHub repository (`VijayabaskarR-06/welcome-wave` or `realRoronoa/Welcome_Wave`).
2. Render reads [render.yaml](../render.yaml) and creates `welcome-wave-api` (`plan: free`, `runtime: docker`).
3. Fill in the `sync: false` environment variables in Render:
   - **Database:** `DATABASE_URL_OWNER`, `APP_DB_PASSWORD`, `DATABASE_URL`
   - **App URL:** `PUBLIC_URL` (set to your Vercel URL once deployed, e.g. `https://welcome-wave.vercel.app`)
   - **Auth (free Firebase Spark plan):** `FIREBASE_PROJECT_ID`, `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`
   - **AI:** `GEMINI_API_KEY`
   - **Optional:** `GITHUB_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `SMTP_*`, `AGORA_*`, `MURF_API_KEY`, `VOICE_TOOL_URL`
4. Deploy and check `https://<your-render-service>.onrender.com/health/ready` returns `{"ok": true}`.

### Quick demo-mode test on Render (no Neon or Firebase required)
If you just want a live demo backend first, set `MODE=demo` and `ENV=dev` in Render's Environment tab (leave `DATABASE_URL*` and `FIREBASE_*` empty) and redeploy.

---

## 3. Frontend on Vercel (Free Hobby Plan)

1. If your Render backend URL is anything other than `https://welcome-wave-api.onrender.com`, update the three `destination` URLs in [frontend/vercel.json](../frontend/vercel.json).
2. Go to [vercel.com/new](https://vercel.com/new), import the GitHub repository, and set:
   - **Root Directory:** `frontend`
   - **Framework Preset:** `Vite`
3. Deploy. Vercel reads [frontend/vercel.json](../frontend/vercel.json) for build, rewrites, and CSP/security headers.
4. Copy your live `https://<your-app>.vercel.app` domain and:
   - Set `PUBLIC_URL=https://<your-app>.vercel.app` in Render's Environment tab (triggers a redeploy).
   - Add `<your-app>.vercel.app` under **Firebase Console → Authentication → Settings → Authorized domains**.
   - If using Google Calendar/Drive OAuth, add `https://<your-app>.vercel.app/api/integrations/google/callback` to your Google OAuth client's authorized redirect URIs.
