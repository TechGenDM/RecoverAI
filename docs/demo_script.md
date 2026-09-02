# RecoverAI — Live Demo & Evaluation Runbook (Rev 4 Frozen)

> Razorpay AI Revenue Recovery Buildathon
> Status: Approved Demo Specification

---

## 1. Demo Dual-Path Architecture

To satisfy the buildathon evaluation requirements while strictly respecting Razorpay sandbox rate limits and hygiene, RecoverAI operates across two clearly separated execution paths:

```
                      ┌───────────────────────────────────────────────┐
                      │              RecoverAI Engine                 │
                      └───────────────────────┬───────────────────────┘
                                              │
                     ┌────────────────────────┴────────────────────────┐
                     ▼                                                 ▼
          [Live Demonstration]                             [Synthetic Evaluation]
          Mode: LIVE                                       Mode: SIMULATED
          • 1–2 real Razorpay test payments                • 20+ realistic synthetic payment failures
          • Real Payment Links dispatched                  • Simulated executor (zero API calls)
          • Real webhooks received and processed           • Statistical metrics across full batch
          • Live state machine transitions shown           • Recovery Rate, ROI, Prevented Churn
```

---

## 2. Evaluation Scenario Suite (20 Scenarios)

The simulated evaluation uses 20 realistic payment failure scenarios, covering:

1. **Bank Technical Outage / Downtime** (HDFC / SBI / ICICI gateway downtime)
   - *Suggested Strategy*: `WAIT` with dynamic cooldown (2–4 hours).
2. **Insufficient Funds on Card / UPI**
   - *Suggested Strategy*: `WAIT` (cooldown until typical salary/evening hours) or `SEND_PAYMENT_LINK`.
3. **Authentication / 3DS Timeout / OTP Drop**
   - *Suggested Strategy*: `SEND_PAYMENT_LINK` (immediate payment link with alternate options).
4. **Card Expired / Limit Exceeded**
   - *Suggested Strategy*: `SEND_PAYMENT_LINK` (prompt for alternate payment method).
5. **Repeated Gateway Failure / Anomaly**
   - *Suggested Strategy*: `ESCALATE` (manual intervention for high-value orders).
6. **Futile / Fraudulent / Stolen Card**
   - *Suggested Strategy*: `STOP` (immediate stop to prevent fraud).

---

## 3. Measured Recovery Metrics

The dashboard calculates and displays:

- **Gross Revenue at Risk**: Total paise of failed payments.
- **Gross Revenue Recovered**: Total paise captured via verified recovery payments (`recovered_payment_id`).
- **Recovery Rate (%)**: $\frac{\text{Recovered Revenue}}{\text{Revenue at Risk}} \times 100$.
- **Average Cycles to Recovery**: Mean attempt count for successfully recovered cases.
- **Agent vs Policy Alignment**: Percentage of cases where the LLM proposal was approved without modification.
