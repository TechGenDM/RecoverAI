from app.models import (
    AuditEvent,
    Customer,
    Payment,
    RecoveryAction,
    RecoveryCase,
    RecoveryDecision,
    WebhookEvent,
)


def test_models_importable():
    # If this file runs, it means the models can be imported without Circular Dependencies
    # or syntax errors.
    assert Customer.__tablename__ == "customers"
    assert Payment.__tablename__ == "payments"
    assert RecoveryCase.__tablename__ == "recovery_cases"
    assert RecoveryDecision.__tablename__ == "recovery_decisions"
    assert RecoveryAction.__tablename__ == "recovery_actions"
    assert WebhookEvent.__tablename__ == "webhook_events"
    assert AuditEvent.__tablename__ == "audit_events"
