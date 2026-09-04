from datetime import UTC, datetime, timedelta

from app.schemas import RecoveryContext, RecoveryDecisionSchema
from app.services.safety_validator import (
    compute_heuristic_likelihood,
    validate_decision,
)


def test_heuristic_computation():
    # Known values
    assert compute_heuristic_likelihood("invalid_otp") == 0.60
    assert compute_heuristic_likelihood("fraud_suspected") == 0.05
    # Default for unknown
    assert compute_heuristic_likelihood("unknown_error_code") == 0.30
    assert compute_heuristic_likelihood(None) == 0.30

def get_dummy_context(window_expired=False, can_send_link=True, attempt_number=1, hours_remaining=24.0):
    from app.schemas import (
        CustomerContext,
        HistoricalContext,
        PaymentContext,
        RecoveryHistory,
        TimeContext,
    )
    return RecoveryContext(
        case_id="case-123",
        attempt_number=attempt_number,
        payment=PaymentContext(
            payment_id="pay_123",
            amount=1000,
            currency="INR",
            method="card",
            bank=None,
            card_network="Visa",
            international=False,
            order_id=None,
            error_code=None,
            error_reason="insufficient_funds",
            error_source=None,
            error_step=None,
            failed_at=datetime.now(UTC).isoformat()
        ),
        customer=CustomerContext(has_email=True, has_phone=True, has_profile=False),
        historical=HistoricalContext(
            total_payments=0, successful_payments=0, failed_payments=1, success_rate=None,
            has_previously_paid=False, has_previously_failed=True, avg_payment_amount=None,
            days_since_last_payment=None, repeated_method_failures=0
        ),
        recovery_history=RecoveryHistory(attempt_count=attempt_number - 1, previous_actions=[]),
        time=TimeContext(
            failed_at=datetime.now(UTC).isoformat(),
            evaluation_time=datetime.now(UTC).isoformat(),
            recovery_window_expires_at=(datetime.now(UTC) + timedelta(hours=hours_remaining)).isoformat(),
            hours_remaining=hours_remaining,
            hours_since_failure=0.1,
            window_expired=window_expired
        ),
        can_generate_payment_link=can_send_link,
        can_auto_notify_customer=can_send_link,
        max_attempts=3
    )

def test_safety_valid_send_link():
    context = get_dummy_context()
    decision = RecoveryDecisionSchema(
        case_id="case-123", action="SEND_PAYMENT_LINK", delay_hours=0,
        llm_confidence=0.9, reason="looks good", risk_factors=[]
    )
    res = validate_decision(decision, context)
    assert res.policy_verdict == "ALLOW"
    assert res.effective_action == "SEND_PAYMENT_LINK"

def test_safety_max_attempts():
    # attempt_number = 4, max is 3
    context = get_dummy_context(attempt_number=4)
    decision = RecoveryDecisionSchema(
        case_id="case-123", action="SEND_PAYMENT_LINK", delay_hours=0,
        llm_confidence=0.9, reason="try again", risk_factors=[]
    )
    res = validate_decision(decision, context)
    assert res.policy_verdict == "DENY"
    assert res.effective_action == "STOP"
    assert "Exceeded max attempts" in res.policy_reason

def test_safety_window_expired():
    context = get_dummy_context(window_expired=True, hours_remaining=0.0)
    decision = RecoveryDecisionSchema(
        case_id="case-123", action="WAIT", delay_hours=2,
        llm_confidence=0.9, reason="wait", risk_factors=[]
    )
    res = validate_decision(decision, context)
    assert res.policy_verdict == "DENY"
    assert res.effective_action == "STOP"

def test_safety_no_contact_info():
    context = get_dummy_context(can_send_link=False)
    decision = RecoveryDecisionSchema(
        case_id="case-123", action="SEND_PAYMENT_LINK", delay_hours=0,
        llm_confidence=0.9, reason="send it", risk_factors=[]
    )
    res = validate_decision(decision, context)
    assert res.policy_verdict == "MODIFY"
    assert res.effective_action == "WAIT"

def test_safety_delay_capping():
    context = get_dummy_context(hours_remaining=10.0)
    decision = RecoveryDecisionSchema(
        case_id="case-123", action="WAIT", delay_hours=20.0,
        llm_confidence=0.9, reason="wait long", risk_factors=[]
    )
    res = validate_decision(decision, context)
    assert res.policy_verdict == "MODIFY"
    assert res.effective_action == "WAIT"
    assert res.delay_hours == 9.9
