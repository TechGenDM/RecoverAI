# RecoverAI — Agent Contract & Policy Bounds (Rev 4 Frozen)

> Razorpay AI Revenue Recovery Buildathon · V1 Scope: Failed Payment Recovery
> Status: Approved Agent Specification

---

## 1. Design Rationale & Modifications

| Original Concept | Rev 4 Frozen Spec | Reason |
| :--- | :--- | :--- |
| `expectedRecoveryProbability` in LLM output | **Removed** | LLMs cannot produce calibrated Bayesian probabilities |
| `confidence: number` (unbounded) | `llmConfidence: number [0.0, 1.0]` | Internal/audit only — strictly NOT shown in merchant UI |
| `customerContactAvailable` (binary) | `canGeneratePaymentLink`, `canAutoNotifyCustomer` | Contact details are not required to generate payment links |
| No explicit stop signal | `stopRecommended: boolean` | Belt-and-suspenders signal to prevent unnecessary retries |
| `merchantConfig: MerchantRecoveryPolicy` in prompt | **Removed from prompt** | Single-merchant V1; policy rules live in deterministic code |

---

## 2. RecoveryContext (Input to LLM Agent)

The Context Builder queries the database and assembles this structured payload for the LLM:

```typescript
interface RecoveryContext {
  // Case identity (echoed back in decision for binding)
  caseId: string;                       // Internal UUID
  attemptNumber: number;                // 1-indexed

  // Original payment
  originalPaymentId: string;            // Razorpay pay_xxx — never modified
  amount: number;                       // In paise
  currency: string;                     // e.g. "INR"
  paymentMethod: string;                // "card" | "upi" | "netbanking" | "wallet"

  // Failure context
  errorCode: string;                    // e.g. "BAD_REQUEST_ERROR"
  errorReason: string;                  // e.g. "insufficient_funds"
  errorSource: string;                  // "customer" | "bank" | "razorpay"
  errorStep: string;                    // e.g. "payment_authentication"

  // Recovery viability flags (policy pre-computed — not LLM's job to determine)
  canGeneratePaymentLink: boolean;      // true unless structurally blocked by policy
  canAutoNotifyCustomer: boolean;       // true only if email OR phone is known

  // Time constraints (computed at context-build time)
  hoursSinceFailure: number;
  hoursRemainingInWindow: number;       // MAX_WINDOW - elapsed
  maxAttempts: number;                  // From config

  // Prior attempts (so LLM can reason about escalating strategy)
  previousActions: PreviousAction[];
}

interface PreviousAction {
  attemptNumber: number;
  actionType: "WAIT" | "SEND_PAYMENT_LINK" | "ESCALATE" | "STOP";
  executedAt: string;                   // ISO 8601
  outcome: "RECOVERED" | "EXPIRED" | "CANCELLED" | "FAILED" | "PENDING";
}
```

---

## 3. RecoveryDecision (Output from LLM Agent)

The LLM returns this exact schema, validated strictly via Pydantic:

```typescript
interface RecoveryDecision {
  // Must echo caseId — validated before any action is taken
  caseId: string;

  // The recommended action
  action: "WAIT" | "SEND_PAYMENT_LINK" | "ESCALATE" | "STOP";

  // Required when action === "WAIT" — policy engine rejects if absent
  delayHours?: number;

  // LLM classification confidence — internal/audit metadata only
  // NOT shown in merchant-facing UI. NOT used by policy engine.
  llmConfidence: number;               // [0.0, 1.0]

  // Explainability fields — shown in merchant UI
  reason: string;                      // Primary rationale
  riskFactors: string[];               // Factors considered

  // Belt-and-suspenders stop signal
  stopRecommended: boolean;            // true if agent believes recovery is futile
}
```

---

## 4. Deterministic Policy Engine Rules

The Policy Engine receives the LLM's `RecoveryDecision` alongside the `RecoveryContext` and issues an **ALLOW**, **MODIFY**, or **DENY** verdict:

1. **Attempt Bound Check**:
   - If `context.attemptNumber >= config.RECOVERY_MAX_ATTEMPTS`:
   - Override to `STOP` (verdict: `MODIFY`, reason: `MAX_ATTEMPTS_EXCEEDED`).
2. **Window Expiry Check**:
   - If `context.hoursRemainingInWindow <= 0`:
   - Override to `STOP` (verdict: `MODIFY`, reason: `RECOVERY_WINDOW_EXPIRED`).
3. **Link Expiry Feasibility**:
   - If action is `SEND_PAYMENT_LINK` but `hoursRemainingInWindow < config.RECOVERY_LINK_EXPIRY_HOURS`:
   - Either reduce link expiry to match window or override to `STOP`.
4. **WAIT Parameter Validation**:
   - If action is `WAIT` and `delayHours` is missing or `<= 0`:
   - Override with default cooldown (e.g. 4 hours) (verdict: `MODIFY`, reason: `INVALID_WAIT_DELAY`).
5. **Structural Pre-condition Check**:
   - If action is `SEND_PAYMENT_LINK` and `canGeneratePaymentLink` is `false`:
   - Override to `ESCALATE` (verdict: `MODIFY`, reason: `LINK_GENERATION_UNAVAILABLE`).

---

## 5. Explainability & UI Presentation Rules

```
Merchant UI Case Detail Display:
✓ Effective Action             (from effective_action after policy)
✓ Policy Verdict               (ALLOW / MODIFY / DENY + policy_reason)
✓ Agent Rationale              (decision.reason)
✓ Risk Factors                 (decision.risk_factors)
✓ Heuristic Recovery Estimate  (heuristic_recovery_likelihood — explicitly labeled "Rule-based heuristic")
✗ LLM Confidence               (NEVER displayed to merchant — audit log only)
✗ Recovery Probability         (NEVER displayed to merchant — no calibrated model exists)
```
