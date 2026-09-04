from app.config import settings
from app.schemas import RecoveryContext, RecoveryDecisionSchema, SafetyResult

RECOVERY_LIKELIHOOD_HEURISTICS: dict[str, float] = {
    "insufficient_funds":      0.35,
    "invalid_otp":             0.60,
    "gateway_technical_error": 0.55,
    "card_declined":           0.25,
    "payment_cancelled":       0.45,
    "fraud_suspected":         0.05,
    "network_error":           0.50,
    "bank_unavailable":        0.45,
    "authentication_failed":   0.40,
}
DEFAULT_HEURISTIC = 0.30

def compute_heuristic_likelihood(error_reason: str | None) -> float:
    """Deterministic heuristic — NOT a calibrated probability."""
    if not error_reason:
        return DEFAULT_HEURISTIC
    return RECOVERY_LIKELIHOOD_HEURISTICS.get(
        error_reason.lower(), DEFAULT_HEURISTIC
    )

def validate_decision(decision: RecoveryDecisionSchema, context: RecoveryContext) -> SafetyResult:
    action = decision.action
    delay_hours = decision.delay_hours

    # 1. Hard limits on attempts
    if context.attempt_number > settings.RECOVERY_MAX_ATTEMPTS and action != "STOP":
        return SafetyResult(
            effective_action="STOP",
                policy_verdict="DENY",
                policy_reason=f"Exceeded max attempts ({settings.RECOVERY_MAX_ATTEMPTS})",
                policy_modification_detail={"original_action": action},
                delay_hours=None
            )
            
    # 2. Window expiration
    if context.time.window_expired and action != "STOP":
        return SafetyResult(
            effective_action="STOP",
                policy_verdict="DENY",
                policy_reason="Recovery window expired",
                policy_modification_detail={"original_action": action},
                delay_hours=None
            )

    # 3. Can't send link if no contact info
    if action == "SEND_PAYMENT_LINK" and not context.can_generate_payment_link:
        return SafetyResult(
            effective_action="WAIT",
            policy_verdict="MODIFY",
            policy_reason="Cannot send payment link: no email or phone",
            policy_modification_detail={"original_action": action},
            delay_hours=24.0 # Wait for profile update?
        )

    # 4. Cap delay_hours to window remaining
    if action == "WAIT" and delay_hours > context.time.hours_remaining:
        adjusted_delay = max(0.1, context.time.hours_remaining - 0.1)
        return SafetyResult(
                effective_action="WAIT",
                policy_verdict="MODIFY",
                policy_reason="Capped delay_hours to fit within recovery window",
                policy_modification_detail={"original_delay": delay_hours, "new_delay": adjusted_delay},
                delay_hours=adjusted_delay
            )
            
    return SafetyResult(
        effective_action=action,
        policy_verdict="ALLOW",
        policy_reason="Passed all safety checks",
        policy_modification_detail=None,
        delay_hours=delay_hours if action == "WAIT" else None
    )
