from pydantic import BaseModel


class PaymentContext(BaseModel):
    payment_id: str
    amount: int
    currency: str
    method: str | None
    bank: str | None
    card_network: str | None
    international: bool
    order_id: str | None
    error_code: str | None
    error_reason: str | None
    error_source: str | None
    error_step: str | None
    failed_at: str


class CustomerContext(BaseModel):
    has_email: bool
    has_phone: bool
    has_profile: bool


class HistoricalContext(BaseModel):
    total_payments: int
    successful_payments: int
    failed_payments: int
    success_rate: float | None
    has_previously_paid: bool
    has_previously_failed: bool
    avg_payment_amount: float | None
    days_since_last_payment: float | None
    repeated_method_failures: int


class PreviousAction(BaseModel):
    attempt_number: int
    action_type: str
    executed_at: str | None
    outcome: str | None


class RecoveryHistory(BaseModel):
    attempt_count: int
    previous_actions: list[PreviousAction]


class TimeContext(BaseModel):
    failed_at: str
    evaluation_time: str
    recovery_window_expires_at: str
    hours_remaining: float
    hours_since_failure: float
    window_expired: bool


class RecoveryContext(BaseModel):
    case_id: str
    attempt_number: int
    payment: PaymentContext
    customer: CustomerContext
    historical: HistoricalContext
    recovery_history: RecoveryHistory
    time: TimeContext
    can_generate_payment_link: bool
    can_auto_notify_customer: bool
    max_attempts: int
