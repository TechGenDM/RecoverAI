# RecoverAI — Bounded AI Revenue Recovery Agent

<div align="center">

![RecoverAI Status](https://img.shields.io/badge/Status-Milestone%203%20Complete-success?style=for-the-badge)
![Tests](https://img.shields.io/badge/Tests-71%20Passing-brightgreen?style=for-the-badge)
![Audit](https://img.shields.io/badge/Production%20Audit-15%2F15%20Verified-blue?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-15-black?style=for-the-badge&logo=next.js&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=for-the-badge&logo=typescript&logoColor=white)

**An enterprise-grade, deterministic, bounded AI revenue-recovery agent designed for the Razorpay AI Revenue Recovery Buildathon.**

[Architecture](./docs/architecture.md) • [Agent Contract](./docs/agent_contract.md) • [Demo Script](./docs/demo_script.md) • [Milestones](#-implementation-roadmap)

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
│   │   └── models/                # SQLAlchemy 2.x declarative models
│   │       ├── customer.py
│   │       ├── payment.py
│   │       ├── recovery_case.py
│   │       ├── recovery_decision.py
│   │       ├── recovery_action.py
│   │       ├── webhook_event.py
│   │       └── audit_event.py
│   └── tests/                     # Unit and integration test suites
│       ├── test_main.py           # Health check and config tests
│       └── test_models.py         # Declarative model validation tests
└── frontend/                      # Next.js 15 / React / TypeScript App
    ├── .env.local.example         # Frontend environment template
    ├── package.json               # Frontend dependencies and scripts
    ├── tsconfig.json              # TypeScript configuration
    ├── eslint.config.mjs          # Next.js ESLint configuration
    └── src/app/                   # App router pages, layouts, and styles
        ├── layout.tsx             # Root HTML layout
        ├── page.tsx               # Recovery dashboard placeholder
        └── globals.css            # Base stylesheet
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
Open [http://localhost:3000](http://localhost:3000) to view the Next.js application.

---

## 🚦 Implementation Roadmap

| Milestone | Scope | Deliverables | Status |
| :--- | :--- | :--- | :---: |
| **M0** | **Foundation & Setup** | Monorepo layout, Docker PostgreSQL, SQLAlchemy 2.x models, Alembic migrations, FastAPI health check, Next.js bootstrap, base test suite | **Done** ✅ |
| **M1** | **Ingestion & Cases** | Razorpay webhook signature verification, event deduplication, customer upsert, payment failure ingestion, case creation (CREATED status), audit logging | **Implemented** ✅ |
| **M2** | **Context & Diagnostic Agent** | Failure context builder, LLM prompt engineering, Gemini/OpenAI adapter, structured strategy proposal | **Implemented** ✅ |
| **M3** | **Policy Engine & Executor** | Deterministic boundary checks, cooldown guards, Payment Link generation, reference ID generator, retry throttling | Scheduled ⏳ |
| **M4** | **Reconciliation & Scheduler** | State machine transitions, async scheduler loop, late payment handling, recovery verification | Scheduled ⏳ |
| **M5** | **Synthetic Batch & Metrics** | 20+ realistic payment failure scenarios, simulated engine, recovery rate, ROI, prevented-churn analytics | Scheduled ⏳ |
| **M6** | **Frontend Dashboard & Demo** | Metric cards, live case explorer, audit trail inspector, interactive simulator, end-to-end demo flow | Scheduled ⏳ |

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
