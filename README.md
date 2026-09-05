# RecoverAI — Bounded AI Revenue Recovery Agent

<div align="center">

![RecoverAI Status](https://img.shields.io/badge/Status-Milestone%205%20Verified%20--%20Production%20Live-success?style=for-the-badge)
![Tests](https://img.shields.io/badge/Tests-113%20Passing-brightgreen?style=for-the-badge)
![Live Frontend](https://img.shields.io/badge/Vercel-Live%20Dashboard-black?style=for-the-badge&logo=vercel)
![Live Backend](https://img.shields.io/badge/Railway-Live%20API-blueviolet?style=for-the-badge&logo=railway)
![Audit](https://img.shields.io/badge/Production%20Audit-Verified-blue?style=for-the-badge)
![Determinism](https://img.shields.io/badge/Determinism-SHA--256%20100%25-9cf?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-black?style=for-the-badge&logo=next.js&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white)

**An enterprise-grade, deterministic, bounded AI revenue-recovery agent designed for the Razorpay AI Revenue Recovery Buildathon.**

[Live Dashboard](https://frontend-pearl-beta-dgjifr4fvi.vercel.app) • [Live API](https://backend-production-740b3.up.railway.app) • [Architecture](./docs/architecture.md) • [Agent Contract](./docs/agent_contract.md) • [Deployment Guide](./docs/deployment.md) • [Demo Script](./docs/demo_script.md) • [Milestones](#-implementation-roadmap)

</div>

---

## 🎯 Executive Summary

**RecoverAI** intercepts failed payment events from Razorpay, performs deep failure diagnostics (bank downtime, insufficient funds, network drops, auth failures), recommends recovery interventions via an LLM agent, enforces strict compliance and monetary constraints through a deterministic **Policy Engine**, and executes bounded actions with complete auditability.

```
Failed Razorpay Payment
         ↓
  Webhook Intake (Async boundary: Verify signature → Deduplicate → Persist → 2xx)
         ↓
  Database Scheduler (Phase 1: Atomically claims CREATED / WAITING cases)
         ↓
  Context Builder (Failure telemetry, customer history, attempt history, remaining window)
         ↓
  LLM Diagnostic Agent (Gemini 2.0 Flash / Mock: Proposes structured recovery strategy)
         ↓
  Deterministic Policy Engine (Hard validation: Max attempts, window expiry, contact check)
         ↓
  Recovery Executor (Category B Reconciliation → Category A Execution → Payment Links)
         ↓
  Payment Reconciliation (Tracks recovery with distinct recovered_payment_id)
```

### 🌐 Live Production Deployments

| Component | Platform | Live URL | Verification Status |
| :--- | :--- | :--- | :--- |
| **Executive Dashboard (Frontend)** | Vercel | [https://frontend-pearl-beta-dgjifr4fvi.vercel.app](https://frontend-pearl-beta-dgjifr4fvi.vercel.app) | **Live & Browser Verified** |
| **Recovery Agent Engine (Backend)** | Railway | [https://backend-production-740b3.up.railway.app](https://backend-production-740b3.up.railway.app) | **Live & API Verified** (`/health`) |
| **Relational Ledger (Database)** | Railway | Managed PostgreSQL 16 (Async SQLAlchemy / Alembic) | **Active & Migrated** |

---

## 🏛️ System Architecture & State Machine

```mermaid
flowchart TD
    subgraph WebhookLayer["1. Fast Webhook Boundary (M1)"]
        WH[Razorpay Webhook: payment.failed] -->|raw-body HMAC verification| EP["POST /v1/webhooks/razorpay"]
        EP -->|Idempotent Event Log| WHTable[(webhook_events)]
        EP -->|Initial Case Record| RCTable[(recovery_cases: CREATED)]
        EP -->|Instant 2xx Response| WHResp[Return HTTP 200 OK]
    end

    subgraph AnalysisEngine["2. Diagnostic & Policy Engine (M2)"]
        SCHED[Async Scheduler: claim_batch] -->|FOR UPDATE SKIP LOCKED| RCTable
        RCTable -.->|Transitions to ANALYSING| SCHED
        SCHED --> CB[Context Builder]
        CB --> LLM[Bounded LLM Agent: Gemini 2.0]
        LLM -->|Strategy Proposal| PE[Deterministic Policy Engine]
        PE -->|ALLOW / MODIFY / DENY| DEC[(recovery_decisions)]
    end

    subgraph RecoveryExecutor["3. Recovery Executor & Reconciliation (M3)"]
        EXEC_SCHED[M3 Discovery / Executor] -->|Category B: Reconcile EXECUTING| RECON_B[GET /v1/payment_links/?reference_id]
        EXEC_SCHED -->|Category A: Claim Decisions| CLAIM[Atomic Claim & Transition]
        CLAIM -->|Zero DB Txn during HTTP| RZP_API[Razorpay API POST /v1/payment_links]
        RZP_API -->|Payment Link Generated| ACT[(recovery_actions)]
        ACT -->|Transitions to LINK_SENT| RCTable
        RZP_API -.->|Unknown / Timeout| UNK[Leaves Case EXECUTING]
    end

    subgraph WebhookRecon["4. Payment Reconciliation (M3)"]
        RZP_PAID[Razorpay Webhook: payment_link.paid] --> RC_SYNC[Reconcile Payment Event]
        RC_SYNC -->|Validate Amount & Window| RCTable
        RC_SYNC -->|Mark Case RECOVERED| RCTable
        RC_SYNC -->|Record recovered_payment_id| PAYTable[(payments)]
    end
```

### Complete 7-State Case Lifecycle

```mermaid
stateDiagram-v2
    [*] --> CREATED: payment.failed webhook
    CREATED --> ANALYSING: M2 Scheduler claims case
    ANALYSING --> WAITING: Policy Engine delays execution (due_at set)
    WAITING --> ANALYSING: due_at passed & re-claimed
    ANALYSING --> EXECUTING: Decision = SEND_PAYMENT_LINK (M3 claims)
    ANALYSING --> STOPPED: Decision = STOP / Policy DENY
    EXECUTING --> LINK_SENT: Payment Link successfully created / reconciled
    EXECUTING --> STOPPED: Window expired / Multiple links anomaly
    LINK_SENT --> RECOVERED: payment_link.paid webhook received
    LINK_SENT --> STOPPED: Window expires without payment
    STOPPED --> RECOVERED: Late payment_link.paid with in-window timestamp
```

### Core Invariants & Production Safety Guarantees

1. **Async Webhook Boundary**: Webhook handlers verify signatures, deduplicate events, persist state, and return HTTP 2xx immediately. No LLM or external calls are executed in the webhook path.
2. **Deterministic Policy Engine**: The LLM agent is advisory only. Every action must satisfy strict constraints (max attempts, max window hours, link expiry cap, merchant thresholds) before execution.
3. **Strict Payment Separation**: The original failed payment (`original_payment_id`) is never marked as paid. A distinct successful payment (`recovered_payment_id`) is tracked and verified.
4. **Bounded Reference Length**: All Razorpay `reference_id` strings follow the deterministic formula `rc-{case_id_hex[:24]}-a{n}` (≤ 31 characters), strictly respecting Razorpay's 40-character maximum.
5. **Zero Open DB Transactions During HTTP Calls**: All database row locks and transactions commit *before* outbound Razorpay HTTP calls begin, completely eliminating connection pool starvation and lock contention.
6. **Category B Reconciliation Precedes Category A Execution**: Any unresolved `EXECUTING` action is reconciled via `GET /v1/payment_links/?reference_id=...` before any new link creation is attempted.
7. **Unknown External Outcome Safety**: Timeouts or network failures during link creation leave the action and case in `EXECUTING` state—never `WAITING`—guaranteeing that M2 diagnostic re-analysis is never triggered accidentally.
8. **M2-Owned Attempt Counter**: M3 never increments `RecoveryCase.attempt_count`. The attempt counter is owned strictly by M2, ensuring deterministic idempotency across retries.
9. **Exact Reference Matching & Anomaly Detection**: Reconciliation strictly verifies that retrieved links match `reference_id` and `amount` exactly. Detecting multiple matching links triggers an automated safety stop and alert.
10. **Terminal State Immutability**: `RECOVERED` and `STOPPED` are terminal states. The sole exception is a late `payment_link.paid` webhook where the payment timestamp was inside the allowed recovery window.
11. **Zero Credential DB Persistence**: Merchant and gateway API keys/secrets are never written to the database. They are managed exclusively through environment configurations.
12. **Idempotent Webhook Processing**: Re-delivery of identical webhook event IDs returns HTTP 200 with `duplicate_ignored`, without side effects or duplicate ledger entries.

---

## 🗄️ Database Architecture

RecoverAI uses **PostgreSQL** exclusively with async SQLAlchemy 2.x and Alembic.

| Table | Primary Key | Key Fields & Constraints | Purpose |
| :--- | :--- | :--- | :--- |
| `customers` | UUID | `razorpay_customer_id` (Unique), `email`, `phone`, `name` | Customer profile and contact registry |
| `payments` | UUID | `razorpay_payment_id` (Unique), `amount` (BigInteger paise), `status`, `error_code`, `payload_snapshot` (JSONB) | Immutable ledger of payment attempts |
| `recovery_cases` | UUID | `original_payment_id` (Unique), `payment_fk` (Unique), `status`, `amount_at_risk`, `amount_recovered`, `recovery_window_expires_at` | Central recovery lifecycle state machine |
| `recovery_decisions`| UUID | `case_id`, `attempt_number`, `recommended_action`, `policy_verdict`, `effective_action`, `raw_llm_response` (JSONB) | Full trace of LLM proposals & policy verdicts |
| `recovery_actions` | UUID | `idempotency_key` (Unique), `action_type`, `reference_id`, `provider_resource_id`, `provider_response` (JSONB) | Record of executed interventions |
| `webhook_events` | UUID | `razorpay_event_id` (Unique), `event_type`, `payload` (JSONB), `signature_valid`, `processed` | Idempotent webhook log |
| `audit_events` | UUID | `case_id`, `event_type`, `description`, `details` (JSONB), `created_at` | Append-only compliance and audit log |

---

## 📂 Monorepo Structure

```
RecoverAI/
├── .gitignore                     # Monorepo-wide gitignore
├── docker-compose.yml             # Local PostgreSQL 16 service
├── README.md                      # Primary project documentation
├── docs/                          # Architecture, agent contracts, demo guide
│   ├── architecture.md            # Detailed architecture specification
│   ├── agent_contract.md          # LLM prompt and output schemas
│   └── demo_script.md             # End-to-end demo execution runbook
├── backend/                       # Python 3.12 / FastAPI Backend
│   ├── .env.example               # Backend environment template
│   ├── pyproject.toml             # uv package and dependency configuration
│   ├── pytest.ini                 # Pytest configuration
│   ├── alembic.ini                # Alembic migration configuration
│   ├── alembic/                   # Async migration scripts and versions
│   │   ├── env.py                 # Async SQLAlchemy Alembic runner
│   │   └── versions/              # Migration revision files
│   ├── app/
│   │   ├── config.py              # Pydantic-settings configuration
│   │   ├── database.py            # Async engine and session factories
│   │   ├── main.py                # FastAPI app initialization and lifespan
│   │   ├── models/                # SQLAlchemy 2.x declarative models
│   │   │   ├── customer.py
│   │   │   ├── payment.py
│   │   │   ├── recovery_case.py
│   │   │   ├── recovery_decision.py
│   │   │   ├── recovery_action.py
│   │   │   ├── webhook_event.py
│   │   │   └── audit_event.py
│   │   ├── routers/               # API endpoints
│   │   │   ├── webhooks.py        # Razorpay signature & webhook receiver
│   │   │   ├── scheduler.py       # Batch claims & targeted single-case execution APIs
│   │   │   └── dashboard.py       # Metrics, simulation runner, case detail & timeline
│   │   ├── schemas/               # Pydantic validation & transfer schemas
│   │   │   ├── context.py         # Diagnostic context schema (explicit money representation)
│   │   │   ├── decision.py        # LLM decision & policy schemas
│   │   │   ├── safety.py          # Safety evaluation schemas
│   │   │   └── executor.py        # Execution results & error schemas
│   │   └── services/              # Core business & autonomous logic
│   │       ├── webhook_service.py # Ingestion & webhook reconciliation
│   │       ├── context_builder.py # Failure & customer telemetry aggregator
│   │       ├── analysis_service.py# Diagnostic agent orchestrator
│   │       ├── safety_validator.py# Deterministic Policy Engine
│   │       ├── scheduler.py       # Background claim & tick coordinator
│   │       ├── recovery_service.py# Discovery, reconciliation & execution
│   │       ├── razorpay_client.py # Async Razorpay API client
│   │       ├── llm/               # Pluggable LLM reasoning providers
│   │       │   ├── base.py
│   │       │   ├── gemini_provider.py # Production Gemini 3.6 Flash adapter
│   │       │   └── mock_provider.py
│   │       └── executor/          # Recovery execution backends
│   │           ├── base.py
│   │           ├── live_executor.py
│   │           └── simulated_executor.py
│   └── tests/                     # 113 automated unit & integration tests
│       ├── conftest.py            # Database isolation & test fixtures
│       ├── test_main.py           # Health check and config tests
│       ├── test_models.py         # Declarative model validation tests
│       ├── test_schema_constraints.py # Database constraints verification
│       ├── test_webhook_ingestion.py  # Signature, deduplication, case creation
│       ├── test_context_builder.py    # Failure telemetry & history aggregation
│       ├── test_customer_fallback.py  # Customer contact fallback validation
│       ├── test_money_unit_context.py # Explicit money representation validation
│       ├── test_llm_provider.py       # LLM provider contract tests
│       ├── test_safety_validator.py   # Policy engine boundary checks
│       ├── test_analysis_service.py   # End-to-end diagnostic analysis
│       ├── test_scheduler.py          # Async locking & batch claims
│       ├── test_executor.py           # Atomic execution & link reconciliation
│       ├── test_payment_link_webhooks.py # Paid webhook reconciliation
│       ├── test_metrics.py            # Aggregate metrics API tests
│       ├── test_case_detail.py        # Case detail & sanitized timeline tests
│       └── test_simulation.py         # SHA-256 determinism & simulation tests
└── frontend/                      # Next.js 16 / React 19 / TypeScript App
    ├── .env.local.example         # Frontend local environment template
    ├── .env.production.example    # Frontend production environment template
    ├── package.json               # Frontend dependencies and scripts
    ├── tsconfig.json              # TypeScript configuration
    ├── eslint.config.mjs          # Next.js ESLint configuration
    └── src/
        ├── lib/
        │   └── api.ts             # Strongly-typed RecoverAI API client
        └── app/                   # App router pages, layouts, and styles
            ├── layout.tsx         # Root HTML layout & font loading
            ├── page.tsx           # Executive Revenue Dashboard & Simulation panel
            ├── globals.css        # Base stylesheet
            └── cases/
                ├── page.tsx       # Recovery Cases Explorer
                └── [id]/page.tsx  # Case Detail & Sanitized Audit Trail
```

---

## 🚀 Quickstart & Local Development

### Prerequisites
- **Docker & Docker Compose** (PostgreSQL 16)
- **Python 3.12+** with [`uv`](https://github.com/astral-sh/uv) installed
- **Node.js 18+** & `npm`

### 1. Start Database Infrastructure
```bash
docker compose up -d --wait
```

### 2. Configure Backend Environment
```bash
cd backend
cp .env.example .env
```
Key configuration settings in `backend/.env`:
```ini
DATABASE_URL=postgresql+asyncpg://recoverai:recoverai_password@localhost:5432/recoverai_dev
RAZORPAY_KEY_ID=rzp_test_xxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxx
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.0-flash
GEMINI_API_KEY=xxxxxxx
RECOVERY_MAX_ATTEMPTS=3
RECOVERY_MAX_WINDOW_HOURS=72
RECOVERY_LINK_EXPIRY_HOURS=24
```

### 3. Run Migrations & Backend Server
```bash
# Run database migrations
uv run alembic upgrade head

# Run tests & lint checks
uv run pytest
uv run ruff check

# Start FastAPI development server
uv run uvicorn app.main:app --reload --port 8000
```
Verify the health endpoint:
```bash
curl http://localhost:8000/health
# {"status":"ok","service":"recoverai"}
```

### 4. Run Frontend Dashboard
```bash
cd ../frontend
cp .env.local.example .env.local
npm install
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) to view the RecoverAI Executive Dashboard.

#### Dashboard Capabilities & Views:
- **Executive Revenue Dashboard (`/`)**:
  - **LIVE / SIMULATED Mode Switcher**: Live data isolation with visual sandbox indicator.
  - **Revenue KPI Cards**: Real-time aggregate of Revenue at Risk, Revenue Recovered, Recovery Rate (%), and Payment Links Dispatched.
  - **Recovery Lifecycle Funnel**: Visual tracking of case progression from Ingestion → Policy Analysis → Dispatch → Terminal Recovery.
  - **Recovery Efficiency by Failure Reason**: Granular success metrics comparing recovery rates across error types (issuer downtime, risk check failures, auth timeouts).
  - **Interactive Simulation Control Panel**: Run reproducible batches by providing integer seeds and scenario counts with instant outcome breakdowns.
- **Recovery Cases Explorer (`/cases`)**:
  - Filterable by Mode (`LIVE` / `SIMULATED`) and Status (`ALL`, `RECOVERED`, `LINK_SENT`, `WAITING`, `EXECUTING`, `ANALYSING`, `CREATED`, `STOPPED`, `ESCALATED`).
  - Searchable by payment ID or case ID with full amounts and recovered payment links.
- **Case Detail & Audit Trail (`/cases/[id]`)**:
  - Original failed payment snapshot (error code, reason, source, step, method).
  - Customer contactability channels (PII redacted; email/phone channel flags).
  - M2 Diagnostic Decisions & Policy Engine verdicts (recommended action, effective action, LLM confidence, heuristic likelihood, safety delay).
  - M3 Recovery Actions & Reference IDs (`rc-{case_id_hex[:24]}-a{n}`).
  - Chronological audit event log with sanitized payload metadata.

### 5. Production Deployment (Vercel → Railway)
See the complete step-by-step setup in the [Production Deployment Guide](./docs/deployment.md).
- **Backend on Railway**: Deploy `backend/` connected to Railway Managed PostgreSQL. Set `CORS_ORIGINS=https://your-app.vercel.app` (enforces explicit origins with credentials; never uses wildcard `*`).
- **Frontend on Vercel**: Deploy `frontend/`. Configure `NEXT_PUBLIC_API_BASE_URL=https://your-backend.up.railway.app` (mandatory in production; fails fast and cleanly if omitted).

---

## 🚦 Implementation Roadmap

| Milestone | Scope | Deliverables | Status |
| :--- | :--- | :--- | :---: |
| **M0** | **Foundation & Setup** | Monorepo layout, Docker PostgreSQL, SQLAlchemy 2.x models, Alembic migrations, FastAPI health check, Next.js bootstrap, base test suite | **Done** ✅ |
| **M1** | **Ingestion & Cases** | Razorpay webhook signature verification, event deduplication, customer upsert, payment failure ingestion, case creation (CREATED status), audit logging | **Implemented** ✅ |
| **M2** | **Context & Diagnostic Agent** | Failure context builder, LLM prompt engineering, Gemini 2.0 / Mock adapter, structured strategy proposal, deterministic policy engine, async claim batching | **Implemented** ✅ |
| **M3** | **Recovery Executor & Policy** | Dual-mode executor (LIVE & SIMULATED), atomic claim (`FOR UPDATE SKIP LOCKED`), deterministic `reference_id` reconciliation (`GET /v1/payment_links/?reference_id=...`), Razorpay Payment Links, zero DB transaction during HTTP, webhook reconciliation | **Implemented & Audited** ✅ |
| **M4** | **Observability, Metrics & Dashboard** | Aggregate recovery metrics API, SHA-256 deterministic simulation sandbox, virtual clock isolation (`SIM_EPOCH`), heuristic outcome model, Next.js 16 executive fintech dashboard, cases explorer, case detail & sanitized audit trail | **Implemented & Audited** ✅ |
| **M5** | **Production Hardening & Live E2E Verification** | Real Razorpay Test Mode E2E recovery, genuine payment.failed webhook intake, single-case targeted scheduler APIs, real Gemini 3.6 Flash reasoning, safety validator, Razorpay Payment Link dispatch, payment_link.paid webhook reconciliation, Railway & Vercel deployment, Chrome DevTools Protocol automated verification | **Implemented & Verified** ✅ |
| **M6** | **Final Presentation & Submission Freeze** | Demo walkthrough recording, pitch deck alignment, documentation freeze | Scheduled ⏳ |

---

### 🎯 Real Razorpay Test Mode E2E Verification (Case #4)

RecoverAI executed a 100% genuine live recovery workflow against Razorpay's API and webhooks without synthetic payloads or database overrides:

1. **Original Failed Payment**: `pay_TYRIjF2WPjyOWJ` (₹500 INR / 50,000 paise).
2. **Webhook Intake**: Ingested genuine `payment.failed` event (`event_TYRIjJ9oW09rUv`), signature cryptographically verified via HMAC SHA256, creating `RecoveryCase` `5d705e02-a257-4dfe-87e6-7e00f599d7b7` in `CREATED` status.
3. **M2 Diagnostic Agent**: Invoked Google Gemini (`gemini-3.6-flash`) with explicit money context (`amount_paise: 50000`, `amount_inr: 500.0`, `amount_formatted: "₹500.00"`); recommended `SEND_PAYMENT_LINK`.
4. **Deterministic Policy Engine**: Validated attempt window, customer contactability, and monetary thresholds; issued `ALLOW` verdict with `effective_action: SEND_PAYMENT_LINK`.
5. **M3 Autonomous Execution**: Executed live outbound API call to Razorpay creating Payment Link `plink_TYRS1gu2d7I71d` with deterministic reference `rc-5d705e02a2574dfe87e67e00-a1`, transitioning case to `LINK_SENT`.
6. **Customer Payment & Webhook Reconciliation**: Customer completed payment `pay_TYRU1SPM9aO2VS` on the link. Razorpay dispatched genuine `payment_link.paid` event (`event_TYRU1f7Mfqn8sK`).
7. **Terminal State Transition**: Case transitioned to `RECOVERED` with `amount_recovered: 50000 paise` (`₹500.00`) and `recovered_payment_id: pay_TYRU1SPM9aO2VS`.
8. **Dashboard Verification**: Live Executive Dashboard accurately displays ₹2,000 at risk, ₹500 recovered, and 25.0% recovery rate.

---

## 🧪 Comprehensive Automated Test Suite

RecoverAI features **113 passing automated tests** across all architectural layers, verified under strict Ruff linting and type safety:

```
tests/test_analysis_service.py       .                                 [  0%]
tests/test_case_detail.py            ...                               [  3%]
tests/test_context_builder.py        .                                 [  4%]
tests/test_customer_fallback.py       ......                            [  9%]
tests/test_executor.py               .....................             [ 28%]
tests/test_llm_provider.py           ..                                [ 30%]
tests/test_main.py                   ......                            [ 35%]
tests/test_metrics.py                .......                           [ 41%]
tests/test_models.py                 .                                 [ 42%]
tests/test_money_unit_context.py      .....                             [ 46%]
tests/test_payment_link_webhooks.py  .........                         [ 54%]
tests/test_safety_validator.py       ......                            [ 60%]
tests/test_scheduler.py              ....                              [ 63%]
tests/test_schema_constraints.py     ......                            [ 69%]
tests/test_simulation.py             ................                  [ 83%]
tests/test_webhook_ingestion.py      ...................               [100%]

======================= 113 passed in 26.10s ========================
```

### 15-Point Production Safety Verification Audit
- [x] **M3-01**: `RecoveryCase.attempt_count` is owned exclusively by M2; M3 never increments it.
- [x] **M3-02**: `reference_id` is deterministically derived from stored `attempt_number` (`rc-{case_id_hex[:24]}-a{n}`).
- [x] **M3-03**: Unknown Razorpay outcomes leave action and case in `EXECUTING`; never `WAITING`.
- [x] **M3-04**: Category B reconciliation strictly precedes Category A execution.
- [x] **M3-05**: Exact `GET /v1/payment_links/?reference_id=...` is the primary reconciliation mechanism.
- [x] **M3-06**: Ambiguous GET reconciliation results abort outbound POST link creations.
- [x] **M3-07**: Outbound POST with same `reference_id` executes only when zero matching links exist.
- [x] **M3-08**: Duplicate-reference error from POST acts as a defensive fallback.
- [x] **M3-09**: Reconciliation requires exact `reference_id` string match.
- [x] **M3-10**: Multiple matching links are flagged as an anomaly, halting execution for audit.
- [x] **M3-11**: Zero open DB transactions or row locks during external Razorpay HTTP requests.
- [x] **M3-12**: Full case status re-verification before persisting final execution outcomes.
- [x] **M3-13**: Exact amount verification against `amount_at_risk` on payment receipt.
- [x] **M3-14**: Full in-window verification for late webhook reconciliation.
- [x] **M3-15**: Complete teardown foreign key ordering and database cleanliness.

---

## 🛡️ Security & Compliance Principles

- **No Stored Gateway Secrets**: Database stores only public references and transaction snapshots.
- **HMAC SHA256 Signature Verification**: All inbound webhooks require cryptographic signature verification using the merchant's webhook secret.
- **Fail-Safe Policy Rejections**: If the LLM proposes an unpermitted action, the Policy Engine overrides it to a safe fallback (`WAIT`, `ESCALATE`, or `STOPPED`).
- **Bounded Autonomous Operations**: Autonomous execution is strictly confined to creating payment links with non-partial payments (`accept_partial: false`) and deterministic reference IDs.

---

<div align="center">
  <sub>Built with precision for the Razorpay AI Revenue Recovery Buildathon.</sub>
</div>
