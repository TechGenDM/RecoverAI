# RecoverAI Architecture

*See `implementation_plan.md` in the agent artifacts for the full Rev 4 Architecture.*

## System Overview
RecoverAI is a bounded AI revenue-recovery agent designed for the Razorpay AI Revenue Recovery Buildathon.

The system is composed of:
- **FastAPI Backend**: Manages webhook events, cases, recovery logic, and persistence.
- **PostgreSQL Database**: Holds all application state across 7 core tables.
- **Next.js Frontend**: A dashboard for viewing revenue recovery metrics.

## Key Principles
- **Terminal Immutability**: `RECOVERED` and `STOPPED` states are final, with the single exception of late `payment_link.paid` webhooks inside the recovery window.
- **Fast Webhooks**: Webhooks only persist and immediately return 2xx. A background scheduler handles LLM/Policy processing.
- **Deterministic Syncing**: `idempotency_key` guarantees safe retry logic.
