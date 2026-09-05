from pydantic import BaseModel

class DashboardMetrics(BaseModel):
    mode: str
    
    # Case-Level Funnel Metrics
    total_cases: int
    amount_at_risk_paise: int
    amount_recovered_paise: int
    recovered_cases: int
    stopped_cases: int
    escalated_cases: int
    recovery_rate_by_count: float | None
    recovery_rate_by_amount: float | None
    
    # Interventions (Case-Level, latest decision)
    cases_intervened_link: int
    cases_intervened_wait: int
    cases_intervened_escalate: int
    cases_intervened_stop: int
    
    # Action/Decision Metrics (Attempt-Level)
    payment_links_created: int
    payment_links_paid: int
    payment_links_expired: int
    payment_links_cancelled: int
    
    decisions_total: int
    decisions_send_link: int
    decisions_wait: int
    decisions_escalate: int
    decisions_stop: int
