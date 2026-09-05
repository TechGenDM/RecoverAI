# Production Deployment Guide: Vercel → Railway

This document details the production deployment architecture and environment configuration for deploying **RecoverAI** with the frontend hosted on **Vercel** and the backend + PostgreSQL database hosted on **Railway**.

---

## 🏗️ Architecture Topology

```
┌────────────────────────────────┐         HTTPS / REST API         ┌─────────────────────────────────┐
│         Vercel (Edge)          │ ───────────────────────────────> │        Railway (PaaS)           │
│   Next.js 16 App Router        │                                  │   FastAPI Async Backend         │
│   (Executive Dashboard)        │ <─────────────────────────────── │   (Port 8000 / $PORT)           │
│                                │   Explicit CORS with Credentials │                                 │
└────────────────────────────────┘                                  └────────────────┬────────────────┘
                                                                                     │
                                                                                     │ asyncpg
                                                                                     ▼
                                                                    ┌─────────────────────────────────┐
                                                                    │   Railway Managed PostgreSQL    │
                                                                    │   PostgreSQL 16 (NullPool /     │
                                                                    │   Async Connection Pooling)     │
                                                                    └─────────────────────────────────┘
```

---

## 1. Railway Backend Deployment

### Step A: Provision Managed PostgreSQL
1. Create a new Railway project.
2. Add a **PostgreSQL** database service.
3. Note the internal or public connection URL provided by Railway (`DATABASE_URL`).
   * *Note:* Ensure the scheme is `postgresql+asyncpg://` for SQLAlchemy async engine compatibility. If Railway provides `postgresql://...`, configure your `DATABASE_URL` as:
     ```env
     DATABASE_URL=postgresql+asyncpg://postgres:[PASSWORD]@[HOST]:[PORT]/railway
     ```

### Step B: Deploy FastAPI Service
1. In the same Railway project, deploy from the GitHub repository using the `backend` directory as the Root Directory.
2. Set the Build and Start commands:
   * **Build Command**: `uv sync` (or `pip install -r pyproject.toml`)
   * **Pre-deploy / Release Command**: `uv run alembic upgrade head`
   * **Start Command**: `uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
3. Configure Backend Environment Variables in Railway Dashboard:

| Variable | Required | Description | Example |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | **Yes** | Async PostgreSQL connection string | `postgresql+asyncpg://...` |
| `MODE` | **Yes** | Execution mode (`LIVE` or `SIMULATED`) | `LIVE` |
| `CORS_ORIGINS` | **Yes** | Comma-separated allowed frontend origins | `https://recoverai.vercel.app,http://localhost:3000` |
| `RAZORPAY_KEY_ID` | **Yes** | Merchant key ID from Razorpay Dashboard | `rzp_test_xxxxxx` |
| `RAZORPAY_KEY_SECRET` | **Yes** | Merchant secret | `xxxxxx` |
| `RAZORPAY_WEBHOOK_SECRET` | **Yes** | Webhook verification secret | `xxxxxx` |
| `LLM_PROVIDER` | **Yes** | Diagnostic LLM provider | `gemini` |
| `LLM_MODEL` | **Yes** | Diagnostic model version | `gemini-2.5-flash` |
| `GEMINI_API_KEY` | **Yes** | Google Gemini API Key | `AIzaSy...` |
| `RECOVERY_MAX_ATTEMPTS` | No | Max automated recovery attempts | `3` |
| `RECOVERY_MAX_WINDOW_HOURS` | No | Max hours case remains recoverable | `72` |
| `ENABLE_SIMULATION_ENDPOINT` | No | Allow `/v1/simulation/run` in sandbox | `true` |

4. Generate a public Railway domain for your backend (e.g. `https://recoverai-backend.up.railway.app`).
5. Verify health:
   ```bash
   curl https://recoverai-backend.up.railway.app/health
   # {"status":"ok","service":"recoverai","database":"connected"}
   ```

---

## 2. Vercel Frontend Deployment

### Step A: Link GitHub Repository
1. Import the repository in Vercel.
2. In the Project Settings, set **Root Directory** to `frontend`.
3. Framework Preset will automatically detect **Next.js**.

### Step B: Configure Frontend Environment Variables
Configure in Vercel **Settings → Environment Variables**:

| Variable | Environment | Description | Example |
| :--- | :--- | :--- | :--- |
| `NEXT_PUBLIC_API_BASE_URL` | Production, Preview, Dev | Full URL to the Railway backend service (no trailing slash) | `https://recoverai-backend.up.railway.app` |

> [!IMPORTANT]
> In production (`NODE_ENV === "production"`), `NEXT_PUBLIC_API_BASE_URL` is **mandatory**. If omitted, the client application will immediately throw a configuration error and will **never** silently fall back to `localhost:8000`.

### Step C: Deploy
1. Click **Deploy**.
2. Once deployed, note your production Vercel URL (e.g. `https://recoverai.vercel.app`).
3. Add this Vercel domain to your Railway `CORS_ORIGINS` variable:
   ```env
   CORS_ORIGINS=https://recoverai.vercel.app,https://your-custom-domain.com
   ```

---

## 3. CORS Security & Credentials Verification

* RecoverAI enforces **explicit origin checking** via FastAPI's `CORSMiddleware`.
* Wildcards (`allow_origins=["*"]`) are **never** permitted when `allow_credentials=True`.
* The frontend's `fetchJson` client sends requests with JSON headers and verifies responses.
* When navigating across domains (Vercel $\to$ Railway), preflight `OPTIONS` requests are handled safely by FastAPI and restricted to `CORS_ORIGINS`.

---

## 4. Webhook Ingestion Configuration (Razorpay)

In the [Razorpay Dashboard](https://dashboard.razorpay.com/#/access/webhooks):
1. Add new Webhook URL:
   `https://recoverai-backend.up.railway.app/v1/webhooks/razorpay`
2. Secret: Set matching `RAZORPAY_WEBHOOK_SECRET`.
3. Active Events:
   * `payment.failed` (interception & case creation)
   * `payment_link.paid` (reconciliation & case recovery)
   * `payment_link.expired`
   * `payment_link.cancelled`
