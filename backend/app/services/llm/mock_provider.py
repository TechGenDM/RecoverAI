from app.schemas import RecoveryContext, RecoveryDecisionSchema

from .base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    async def analyze_case(
        self, context: RecoveryContext
    ) -> tuple[RecoveryDecisionSchema, str]:
        # Deterministic logic for testing
        if context.payment.error_reason == "fraud_suspected":
            action = "ESCALATE"
        elif context.time.hours_since_failure > 48:
            action = "STOP"
        elif not context.customer.has_email and not context.customer.has_phone:
            action = "WAIT"
        else:
            action = "SEND_PAYMENT_LINK"

        decision = RecoveryDecisionSchema(
            case_id=context.case_id,
            action=action,
            delay_hours=0.0 if action != "WAIT" else 2.0,
            llm_confidence=0.9,
            reason=f"Mock decision based on {context.payment.error_reason}",
            risk_factors=["mock_risk"],
        )
        return decision, decision.model_dump_json()
