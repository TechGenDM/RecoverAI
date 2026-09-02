# Agent Contract

*See `implementation_plan.md` in the agent artifacts for the full Rev 4 Contract.*

## Goal
The agent is bounded: it MUST NOT hallucinate payment links or actions. It only recommends a strategy, which is then verified against a deterministic Policy Engine.

## Context Building
The agent receives:
- Customer details
- Payment history
- Failure context

## Execution
The agent suggests one of:
- `WAIT` (with delay_hours)
- `RETRY_SAME_METHOD`
- `RETRY_LOWER_AMOUNT`
- `RETRY_ALTERNATE_METHOD`
- `ESCALATE`

The policy engine confirms if this action is safe.
