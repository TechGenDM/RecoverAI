from pydantic import BaseModel, Field


class FailureReasonMetrics(BaseModel):
    total_cases: int = 0
    recovered_cases: int = 0
    recovery_rate_by_count: float | None = None
    amount_at_risk_paise: int = 0
    amount_recovered_paise: int = 0


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

    # Case status distribution (all 8 statuses guaranteed present)
    cases_by_status: dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of cases across all 8 lifecycle statuses",
    )

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

    # Breakdown by original payment failure reason
    recovery_by_failure_reason: dict[str, FailureReasonMetrics] = Field(
        default_factory=dict,
        description="Aggregate recovery performance grouped by payment error reason",
    )
