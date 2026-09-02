import asyncio
import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models import AuditEvent, Customer, Payment, RecoveryCase, WebhookEvent

TEST_SECRET = "test_webhook_secret_xyz123"

test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


def compute_signature(payload_bytes: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()


def generate_payment_failed_payload(
    payment_id: str,
    amount: int = 75000,
    currency: str = "INR",
    customer_id: str | None = "cust_test_123",
    email: str = "customer@example.com",
    contact: str = "+919876543210",
    error_code: str = "BAD_REQUEST_ERROR",
    error_reason: str = "insufficient_funds",
    created_at: int = 1710000000,
) -> dict:
    return {
        "entity": "event",
        "account_id": "acc_test",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount,
                    "currency": currency,
                    "status": "failed",
                    "order_id": "order_test_999",
                    "international": False,
                    "method": "card",
                    "card": {
                        "network": "Visa",
                        "type": "debit",
                        "issuer": "HDFC",
                        "last4": "4242",
                        "secret_token_that_must_be_redacted": "token_12345",
                    },
                    "bank": "HDFC",
                    "email": email,
                    "contact": contact,
                    "customer_id": customer_id,
                    "error_code": error_code,
                    "error_description": "Payment failed due to insufficient balance",
                    "error_source": "customer",
                    "error_step": "payment_authorization",
                    "error_reason": error_reason,
                    "created_at": created_at,
                    "internal_routing_token": "should_be_redacted",
                }
            }
        },
        "created_at": created_at,
    }


@pytest.fixture(autouse=True)
def override_webhook_secret(monkeypatch):
    """Sets a deterministic webhook secret for testing."""
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SecretStr(TEST_SECRET))


@pytest.fixture
async def async_client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# 1. Signature Verification Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_signature(async_client):
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    signature = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "created"
    assert data["payment_id"] == payment_id
    assert "case_id" in data


@pytest.mark.asyncio
async def test_invalid_signature(async_client):
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "invalid_signature_hex_12345",
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_signature"


@pytest.mark.asyncio
async def test_missing_signature(async_client):
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_signature"


@pytest.mark.asyncio
async def test_missing_event_id(async_client):
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    signature = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_event_id"


# ---------------------------------------------------------------------------
# 2. Deduplication & Idempotency Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_event_idempotency(async_client):
    """Submitting the same webhook twice returns HTTP 200 without creating duplicates."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    signature = compute_signature(raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": signature,
        "X-Razorpay-Event-Id": event_id,
    }

    # First delivery
    r1 = await async_client.post(
        "/v1/webhooks/razorpay", content=raw_body, headers=headers
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "created"

    # Second delivery with identical event_id
    r2 = await async_client.post(
        "/v1/webhooks/razorpay", content=raw_body, headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate_ignored"


@pytest.mark.asyncio
async def test_different_event_ids_same_payment_id(async_client):
    """Different event IDs delivering the same payment ID must not create duplicate cases."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event1 = f"evt_{uuid.uuid4().hex[:14]}"
    event2 = f"evt_{uuid.uuid4().hex[:14]}"

    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    r1 = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event1,
        },
    )
    assert r1.status_code == 200
    case_id_1 = r1.json()["case_id"]

    # Delivery with new event_id but same payment_id
    r2 = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event2,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "payment_already_recorded"
    assert r2.json()["case_id"] == case_id_1


@pytest.mark.asyncio
async def test_concurrent_duplicate_delivery(async_client):
    """Concurrent deliveries of the exact same webhook must be atomically resolved."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
        "X-Razorpay-Event-Id": event_id,
    }

    tasks = [
        async_client.post("/v1/webhooks/razorpay", content=raw_body, headers=headers)
        for _ in range(3)
    ]
    responses = await asyncio.gather(*tasks)

    # All responses must be HTTP 200
    for r in responses:
        assert r.status_code == 200

    statuses = [r.json()["status"] for r in responses]
    # Exactly one request created the case, others were duplicate_ignored
    assert statuses.count("created") == 1
    assert statuses.count("duplicate_ignored") == 2


# ---------------------------------------------------------------------------
# 3. Customer & Entity Behavior Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_upsert_and_nullability(async_client):
    """Verify customer upsert when customer_id exists, and handling when customer is absent."""
    # Case A: Missing customer_id
    payment_id_no_cust = f"pay_{uuid.uuid4().hex[:14]}"
    event_no_cust = f"evt_{uuid.uuid4().hex[:14]}"
    payload_no_cust = generate_payment_failed_payload(
        payment_id_no_cust,
        customer_id=None,
        email="anon@example.com",
        contact=None,
    )
    raw_a = json.dumps(payload_no_cust).encode("utf-8")
    r_a = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_a,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_a),
            "X-Razorpay-Event-Id": event_no_cust,
        },
    )
    assert r_a.status_code == 200

    # Case B: With customer_id -> upserts customer record
    cust_id = f"cust_{uuid.uuid4().hex[:10]}"
    payment_id_cust = f"pay_{uuid.uuid4().hex[:14]}"
    event_cust = f"evt_{uuid.uuid4().hex[:14]}"
    payload_cust = generate_payment_failed_payload(
        payment_id_cust,
        customer_id=cust_id,
        email="vip@example.com",
        contact="+919876543210",
    )
    raw_b = json.dumps(payload_cust).encode("utf-8")
    r_b = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_b,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_b),
            "X-Razorpay-Event-Id": event_cust,
        },
    )
    assert r_b.status_code == 200

    # Verify Customer in DB
    async with TestSessionLocal() as session:
        cust_res = await session.execute(
            select(Customer).where(Customer.razorpay_customer_id == cust_id)
        )
        customer = cust_res.scalar_one_or_none()
        assert customer is not None
        assert customer.email == "vip@example.com"
        assert customer.phone == "+919876543210"


# ---------------------------------------------------------------------------
# 4. Recovery Case Initial State & Constraints Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_case_initial_state(async_client):
    """Verify recovery_case is in CREATED status with accurate amounts and window."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    amount = 125000  # 1250.00 INR in paise
    payload = generate_payment_failed_payload(payment_id, amount=amount)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 200
    case_id_str = response.json()["case_id"]
    case_uuid = uuid.UUID(case_id_str)

    async with TestSessionLocal() as session:
        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.id == case_uuid)
        )
        case = case_res.scalar_one()

        assert case.status == "CREATED"
        assert case.attempt_count == 0
        assert case.amount_at_risk == amount
        assert case.amount_recovered == 0
        assert case.recovered_payment_id is None
        assert case.due_at is None
        assert case.resolved_at is None
        assert case.stop_reason is None
        assert case.original_payment_id == payment_id
        assert case.mode == settings.MODE

        # Verify recovery_window_expires_at is failed_at + 72 hours
        expected_window = 1710000000 + (settings.RECOVERY_MAX_WINDOW_HOURS * 3600)
        assert abs(case.recovery_window_expires_at.timestamp() - expected_window) < 2


# ---------------------------------------------------------------------------
# 5. Audit Event & Payload Redaction Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_event_and_payload_redaction(async_client):
    """Verify audit_events creation and payload_snapshot sanitization."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 200
    case_id = uuid.UUID(response.json()["case_id"])

    async with TestSessionLocal() as session:
        # Check AuditEvent
        audit_res = await session.execute(
            select(AuditEvent).where(AuditEvent.case_id == case_id)
        )
        audit = audit_res.scalar_one()
        assert audit.event_type == "PAYMENT_FAILED_INGESTED"
        assert audit.actor == "webhook"
        assert audit.payload["payment_id"] == payment_id
        assert audit.payload["event_id"] == event_id

        # Check Payment payload_snapshot redaction
        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        payment = pay_res.scalar_one()
        snapshot = payment.payload_snapshot

        # Disallowed keys must be stripped
        assert "internal_routing_token" not in snapshot
        assert "secret_token_that_must_be_redacted" not in snapshot.get("card", {})
        # Allowed keys must be preserved
        assert snapshot["card"]["network"] == "Visa"
        assert snapshot["card"]["last4"] == "4242"


# ---------------------------------------------------------------------------
# 6. Error Handling & Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_event_type(async_client):
    """Unsupported event types (e.g. order.paid) must return 200 with status ignored."""
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = {
        "entity": "event",
        "event": "order.paid",
        "contains": ["order"],
        "payload": {"order": {"entity": {"id": "order_123"}}},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_malformed_json_payload(async_client):
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    malformed_body = b"not-a-valid-json-string{["
    sig = compute_signature(malformed_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=malformed_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_json"


@pytest.mark.asyncio
async def test_missing_payment_entity(async_client):
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "payload": {},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    response = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": sig,
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "missing_payment_entity"


@pytest.mark.asyncio
async def test_transaction_rollback_on_failure(async_client):
    """If an unexpected exception occurs during ingestion, transaction rolls back cleanly."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    with patch(
        "app.services.webhook_service.WebhookIngestionService.process_webhook",
        side_effect=RuntimeError("Simulated DB Crash"),
    ):
        response = await async_client.post(
            "/v1/webhooks/razorpay",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
                "X-Razorpay-Event-Id": event_id,
            },
        )
        assert response.status_code == 500
        assert response.json()["error"] == "internal_server_error"

    # Confirm nothing was committed to DB
    async with TestSessionLocal() as session:
        wh_res = await session.execute(
            select(WebhookEvent).where(WebhookEvent.razorpay_event_id == event_id)
        )
        assert wh_res.scalar_one_or_none() is None

        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        assert pay_res.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 7. Proof of Strict Async Boundary (Zero LLM / Zero Outbound API Calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proof_webhook_never_invokes_llm_or_executor(async_client):
    """Critical architectural proof: Webhook path never creates recovery actions, calls LLMs, or invokes executors."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payment_failed_payload(payment_id)
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_signature(raw_body)

    # Webhook execution must only persist to DB and leave case in CREATED status
    with patch("urllib.request.urlopen") as mock_url:
        response = await async_client.post(
            "/v1/webhooks/razorpay",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig,
                "X-Razorpay-Event-Id": event_id,
            },
        )
        assert response.status_code == 200
        assert mock_url.call_count == 0

    case_id = uuid.UUID(response.json()["case_id"])
    async with TestSessionLocal() as session:
        # Case must be strictly in CREATED status
        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.id == case_id)
        )
        case = case_res.scalar_one()
        assert case.status == "CREATED"
        assert case.attempt_count == 0

        # No recovery decisions must exist
        from app.models import RecoveryAction, RecoveryDecision

        dec_res = await session.execute(
            select(RecoveryDecision).where(RecoveryDecision.case_id == case_id)
        )
        assert len(dec_res.scalars().all()) == 0

        # No recovery actions must exist
        act_res = await session.execute(
            select(RecoveryAction).where(RecoveryAction.case_id == case_id)
        )
        assert len(act_res.scalars().all()) == 0
