import hashlib
import hmac
import json
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Customer, Payment, RecoveryCase
from tests.conftest import TestSessionLocal

TEST_SECRET = "test_webhook_secret_fallback_123"


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


def compute_signature(raw_body: bytes) -> str:
    return hmac.new(TEST_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def generate_payload(
    payment_id: str,
    customer_id: str | None = None,
    email: str | None = None,
    contact: str | None = None,
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
                    "amount": 50000,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": f"order_{uuid.uuid4().hex[:14]}",
                    "invoice_id": None,
                    "international": False,
                    "method": "card",
                    "amount_refunded": 0,
                    "refund_status": None,
                    "captured": False,
                    "description": "Customer fallback test",
                    "card_id": None,
                    "bank": None,
                    "wallet": None,
                    "vpa": None,
                    "email": email,
                    "contact": contact,
                    "customer_id": customer_id,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment failed",
                    "error_source": "gateway",
                    "error_step": "payment_authorization",
                    "error_reason": "payment_failed",
                    "created_at": 1788623400,
                }
            }
        },
        "created_at": 1788623400,
    }


@pytest.mark.asyncio
async def test_a_payment_with_razorpay_customer_id_unchanged(async_client):
    """A. Payment with Razorpay customer_id -> existing path unchanged."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    cust_id = f"cust_{uuid.uuid4().hex[:10]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payload(
        payment_id=payment_id,
        customer_id=cust_id,
        email="direct@example.com",
        contact="+919876543210",
    )
    raw = json.dumps(payload).encode("utf-8")
    resp = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw),
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as session:
        cust_res = await session.execute(
            select(Customer).where(Customer.razorpay_customer_id == cust_id)
        )
        customer = cust_res.scalar_one_or_none()
        assert customer is not None
        assert customer.razorpay_customer_id == cust_id
        assert customer.email == "direct@example.com"
        assert customer.phone == "+919876543210"

        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        payment = pay_res.scalar_one()
        assert payment.customer_id == customer.id

        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.original_payment_id == payment_id)
        )
        case = case_res.scalar_one()
        assert case.customer_id == customer.id


@pytest.mark.asyncio
async def test_b_payment_without_customer_id_with_email(async_client):
    """B. Payment without customer_id but with email -> customer fallback works."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payload(
        payment_id=payment_id,
        customer_id=None,
        email="fallback_email@example.com",
        contact=None,
    )
    raw = json.dumps(payload).encode("utf-8")
    resp = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw),
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as session:
        cust_res = await session.execute(
            select(Customer).where(Customer.email == "fallback_email@example.com")
        )
        customer = cust_res.scalar_one_or_none()
        assert customer is not None
        assert customer.razorpay_customer_id is None
        assert customer.email == "fallback_email@example.com"
        assert customer.phone is None

        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        payment = pay_res.scalar_one()
        assert payment.customer_id == customer.id

        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.original_payment_id == payment_id)
        )
        case = case_res.scalar_one()
        assert case.customer_id == customer.id


@pytest.mark.asyncio
async def test_c_payment_without_customer_id_with_phone(async_client):
    """C. Payment without customer_id but with phone -> customer fallback works."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    phone = "+919123456789"
    payload = generate_payload(
        payment_id=payment_id,
        customer_id=None,
        email=None,
        contact=phone,
    )
    raw = json.dumps(payload).encode("utf-8")
    resp = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw),
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as session:
        cust_res = await session.execute(
            select(Customer).where(Customer.phone == phone)
        )
        customer = cust_res.scalar_one_or_none()
        assert customer is not None
        assert customer.razorpay_customer_id is None
        assert customer.email is None
        assert customer.phone == phone

        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        payment = pay_res.scalar_one()
        assert payment.customer_id == customer.id

        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.original_payment_id == payment_id)
        )
        case = case_res.scalar_one()
        assert case.customer_id == customer.id


@pytest.mark.asyncio
async def test_d_payment_with_neither_remains_unavailable(async_client):
    """D. Payment with neither customer_id nor contact -> customer remains unavailable."""
    payment_id = f"pay_{uuid.uuid4().hex[:14]}"
    event_id = f"evt_{uuid.uuid4().hex[:14]}"
    payload = generate_payload(
        payment_id=payment_id,
        customer_id=None,
        email=None,
        contact=None,
    )
    raw = json.dumps(payload).encode("utf-8")
    resp = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw),
            "X-Razorpay-Event-Id": event_id,
        },
    )
    assert resp.status_code == 200

    async with TestSessionLocal() as session:
        pay_res = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == payment_id)
        )
        payment = pay_res.scalar_one()
        assert payment.customer_id is None

        case_res = await session.execute(
            select(RecoveryCase).where(RecoveryCase.original_payment_id == payment_id)
        )
        case = case_res.scalar_one()
        assert case.customer_id is None


@pytest.mark.asyncio
async def test_e_repeated_webhook_and_deduplication(async_client):
    """E. Repeated webhook and shared email reuse does not create duplicate customers."""
    shared_email = f"shared_{uuid.uuid4().hex[:8]}@example.com"
    payment_id_1 = f"pay_{uuid.uuid4().hex[:14]}"
    event_id_1 = f"evt_{uuid.uuid4().hex[:14]}"

    payload_1 = generate_payload(
        payment_id=payment_id_1,
        customer_id=None,
        email=shared_email,
        contact="+919876500001",
    )
    raw_1 = json.dumps(payload_1).encode("utf-8")

    # 1. Ingest payment 1
    resp_1 = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_1,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_1),
            "X-Razorpay-Event-Id": event_id_1,
        },
    )
    assert resp_1.status_code == 200

    # 2. Re-send exact duplicate webhook (deduplication check)
    resp_dup = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_1,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_1),
            "X-Razorpay-Event-Id": event_id_1,
        },
    )
    assert resp_dup.status_code == 200
    assert resp_dup.json()["status"] == "duplicate_ignored"

    # 3. Second distinct payment for same customer email
    payment_id_2 = f"pay_{uuid.uuid4().hex[:14]}"
    event_id_2 = f"evt_{uuid.uuid4().hex[:14]}"
    payload_2 = generate_payload(
        payment_id=payment_id_2,
        customer_id=None,
        email=shared_email,
        contact=None,
    )
    raw_2 = json.dumps(payload_2).encode("utf-8")
    resp_2 = await async_client.post(
        "/v1/webhooks/razorpay",
        content=raw_2,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": compute_signature(raw_2),
            "X-Razorpay-Event-Id": event_id_2,
        },
    )
    assert resp_2.status_code == 200

    # Verify only ONE customer exists with shared_email
    async with TestSessionLocal() as session:
        custs = (
            (
                await session.execute(
                    select(Customer).where(Customer.email == shared_email)
                )
            )
            .scalars()
            .all()
        )
        assert len(custs) == 1
        customer = custs[0]

        # Both payments link to the same customer
        p1 = (
            await session.execute(
                select(Payment).where(Payment.razorpay_payment_id == payment_id_1)
            )
        ).scalar_one()
        p2 = (
            await session.execute(
                select(Payment).where(Payment.razorpay_payment_id == payment_id_2)
            )
        ).scalar_one()
        assert p1.customer_id == customer.id
        assert p2.customer_id == customer.id


@pytest.mark.asyncio
async def test_f_invalid_contact_values_do_not_create_bogus_customers(async_client):
    """F. Invalid / empty contact values do not create bogus customers."""
    test_cases = [
        {"email": "", "contact": ""},
        {"email": "   ", "contact": "   "},
        {"email": "not_an_email", "contact": "123"},
        {"email": "@nodomain", "contact": "phone"},
    ]

    for tc in test_cases:
        payment_id = f"pay_{uuid.uuid4().hex[:14]}"
        event_id = f"evt_{uuid.uuid4().hex[:14]}"
        payload = generate_payload(
            payment_id=payment_id,
            customer_id=None,
            email=tc["email"],
            contact=tc["contact"],
        )
        raw = json.dumps(payload).encode("utf-8")
        resp = await async_client.post(
            "/v1/webhooks/razorpay",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": compute_signature(raw),
                "X-Razorpay-Event-Id": event_id,
            },
        )
        assert resp.status_code == 200

        async with TestSessionLocal() as session:
            pay_res = await session.execute(
                select(Payment).where(Payment.razorpay_payment_id == payment_id)
            )
            payment = pay_res.scalar_one()
            assert payment.customer_id is None, (
                f"Expected None for invalid contacts: {tc}"
            )
