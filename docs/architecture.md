# RecoverAI — Architecture Specification (Rev 4 Frozen)

> Razorpay AI Revenue Recovery Buildathon · V1 Scope: Failed Payment Recovery
> Status: Approved Architecture Specification

---

## 1. System Overview & Core Invariants

```
[Razorpay Test Mode / Production Gateway]
      │
      │  payment.failed webhook
      │  payment_link.paid webhook
      │  payment_link.expired webhook
      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                             FastAPI Backend                               │
│                                                                           │
│  Webhook Ingestion (FAST — returns HTTP 2xx immediately)                  │
│  ├── HMAC-SHA256 raw-body signature verification                          │
│  ├── Event-ID deduplication (webhook_events table)                       │
│  ├── Persist payment + case in CREATED status                             │
│  └── Return HTTP 2xx — NO LLM, NO policy, NO recovery executor            │
│                                                                           │
│  Scheduler / Recovery Processor (Async Background Worker)                 │
│  ├── Poll CREATED cases → trigger analysis pipeline                       │
│  ├── Poll WAITING cases past due_at → trigger re-analysis                 │
│  ├── Force-stop cases past recovery_window_expires_at                    │
│  └── Reconcile stale EXECUTING actions via reference_id                   │
│                                                                           │
│  Recovery Pipeline (invoked ONLY by scheduler)                            │
│  ├── Context Builder  → RecoveryContext (DB assembled)                   │
│  ├── LLMProvider      → RecoveryDecision (single structured call)         │
│  ├── Policy Engine    → Deterministic ALLOW / MODIFY / DENY               │
│  └── Executor         → LiveExecutor (real links) OR SimulatedExecutor    │
│                                                                           │
│  PostgreSQL (Single source of truth & internal task queue)                │
└───────────────────────────────────────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────┐
│     Next.js Frontend     │
│  ├── LIVE panel          │
│  └── SIMULATED panel     │
└──────────────────────────┘
```

---

## 2. The Core Principle: Bounded AI

> **The LLM provides intelligence, not financial authority.**

1. **Webhook handler (synchronous, fast)**:
   - Receives raw webhook payload.
   - Computes HMAC-SHA256 signature using `RAZORPAY_WEBHOOK_SECRET`.
   - Rejects invalid signatures with HTTP 400.
   - Idempotently records event into `webhook_events`. If duplicate `razorpay_event_id`, returns HTTP 200 OK immediately without duplicate processing.
   - Persists initial `Payment` and `RecoveryCase` in `CREATED` status.
   - Returns HTTP 200 OK immediately.
   - **CRITICAL**: The webhook handler never invokes the LLM, policy engine, or external network requests.

2. **Scheduler (asynchronous, DB-driven)**:
   - Periodically queries `recovery_cases WHERE status = 'CREATED'` or `status = 'WAITING' AND due_at <= NOW()`.
   - Transitions case to `ANALYSING`.
   - Context Builder aggregates customer transaction history and error telemetry into `RecoveryContext`.
   - Invokes LLM Agent (single round-trip, Pydantic-validated `RecoveryDecision`).
   - Passes proposal through deterministic **Policy Engine**.
   - Invokes **Recovery Executor** (either `LiveExecutor` or `SimulatedExecutor`).

3. **Outcome Reconciler (synchronous, fast)**:
   - Inbound `payment_link.paid` reconciles against `recovery_actions` by `razorpay_link_id` or `razorpay_link_reference_id`.
   - Case transitions to `RECOVERED`. Sets `recovered_payment_id` and `amount_recovered`.

---

## 3. Strict Payment Separation

The original failed payment is **never** marked as recovered:

```
[Original Payment]                              [Recovery Payment]
  pay_AAAA (status: failed)          ───>         pay_BBBB (status: captured)
       │                                                 │
       ▼                                                 ▼
  recovery_case ───────────────────────────────────────────
       │    original_payment_id = pay_AAAA
       │    recovered_payment_id = pay_BBBB  (set ONLY upon verified payment)
       │    amount_recovered    = BBBB.amount (set ONLY upon verified payment)
       │
       └──< recovery_actions
               razorpay_link_id = plink_XXXX  (bridge between original and recovery)
```

---

## 4. Recovery State Machine Transitions

| Current State | Trigger / Event | Next State | Condition / Notes |
| :--- | :--- | :--- | :--- |
| `CREATED` | Scheduler picks case | `ANALYSING` | Ingestion complete, starting context build |
| `ANALYSING` | Policy engine approves WAIT | `WAITING` | Case scheduled with `due_at = now + delay_hours` |
| `ANALYSING` | Policy engine approves LINK | `LINK_SENT` | Payment Link created, awaiting customer action |
| `ANALYSING` | Policy engine stops | `STOPPED` | Futile failure, max attempts, or window expired |
| `ANALYSING` | Policy engine escalates | `ESCALATED` | High-value anomaly or repeated technical failure |
| `WAITING` | Scheduler detects `due_at <= now` | `ANALYSING` | Delay elapsed, ready for next attempt |
| `LINK_SENT` | `payment_link.paid` webhook | `RECOVERED` | Verified in-window payment received (**Terminal**) |
| `LINK_SENT` | `payment_link.expired` webhook | `ANALYSING` or `STOPPED` | If window/attempts remain -> ANALYSING; else STOPPED |
| `STOPPED` | Late `payment_link.paid` (in-window) | `RECOVERED` | **Sole exception**: late webhook paid within valid window |
| `STOPPED` | Any other event | `STOPPED` | Terminal & Immutable |
| `RECOVERED` | Any event | `RECOVERED` | Terminal & Immutable |
| Any Active | Scheduler expiry sweep | `STOPPED` | `NOW() > recovery_window_expires_at` |

---

## 5. Razorpay Constraints & Idempotency Rules

1. **Reference ID Length**: Razorpay restricts `reference_id` to **≤ 40 characters**. RecoverAI computes reference IDs deterministically:
   $$\text{reference\_id} = \text{rc-} + \text{case\_id.hex[:24]} + \text{-a} + \text{attempt\_number}$$
   Max length: $3 + 24 + 2 + 2 = 31\text{ characters} \le 40$.
2. **Partial Payments**: All recovery payment links are strictly generated with `"accept_partial": false`.
3. **Link Expiry Window**: Link expiry is bounded by the recovery window:
   $$\text{expire\_by} = \min(\text{now} + \text{LINK\_EXPIRY\_HOURS}, \text{recovery\_window\_expires\_at})$$
4. **External Idempotency**: `recovery_actions.idempotency_key` prevents internal double-dispatch. In case of network timeouts creating a payment link, the executor fetches `GET /v1/payment_links?reference_id={ref}` before retrying.
