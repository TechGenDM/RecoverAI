import json
from datetime import UTC, datetime

import pytest

from app.models import RecoveryCase
from app.schemas import PaymentContext
from app.services.context_builder import build_recovery_context
from app.services.llm.gemini_provider import GeminiProvider
from tests.test_safety_validator import get_dummy_context


def test_money_unit_conversions():
    """Verify exact presentation conversion for required test cases."""
    now_str = datetime.now(UTC).isoformat()

    # 100 paise -> ₹1.00
    p1 = PaymentContext(
        payment_id="pay_100",
        amount_paise=100,
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
        failed_at=now_str,
    )
    assert p1.amount_paise == 100
    assert isinstance(p1.amount_paise, int)
    assert p1.amount_inr == 1.00
    assert p1.amount_formatted == "₹1.00"
    assert p1.currency == "INR"

    # 50000 paise -> ₹500.00
    p2 = PaymentContext(
        payment_id="pay_50000",
        amount_paise=50000,
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
        failed_at=now_str,
    )
    assert p2.amount_paise == 50000
    assert isinstance(p2.amount_paise, int)
    assert p2.amount_inr == 500.00
    assert p2.amount_formatted == "₹500.00"
    assert p2.currency == "INR"

    # 12345 paise -> ₹123.45
    p3 = PaymentContext(
        payment_id="pay_12345",
        amount_paise=12345,
        currency="INR",
        method="upi",
        bank=None,
        card_network=None,
        international=False,
        order_id=None,
        error_code=None,
        error_reason="insufficient_funds",
        error_source=None,
        error_step=None,
        failed_at=now_str,
    )
    assert p3.amount_paise == 12345
    assert isinstance(p3.amount_paise, int)
    assert p3.amount_inr == 123.45
    assert p3.amount_formatted == "₹123.45"
    assert p3.currency == "INR"


def test_llm_facing_serialization_never_presents_ambiguous_amount():
    """Verify that serialized LLM context NEVER presents ambiguous 'amount': 50000."""
    context = get_dummy_context()
    # Explicitly set payment to 50000 paise (₹500.00)
    context.payment = PaymentContext(
        payment_id="pay_50000",
        amount_paise=50000,
        currency="INR",
        method="card",
        bank=None,
        card_network="MasterCard",
        international=False,
        order_id=None,
        error_code=None,
        error_reason="insufficient_funds",
        error_source=None,
        error_step=None,
        failed_at=datetime.now(UTC).isoformat(),
    )

    serialized_json = context.model_dump_json()
    parsed = json.loads(serialized_json)

    payment_dict = parsed["payment"]

    # Must contain explicit unit fields
    assert payment_dict["amount_paise"] == 50000
    assert payment_dict["amount_inr"] == 500.0
    assert payment_dict["amount_formatted"] == "₹500.00"
    assert payment_dict["currency"] == "INR"

    # Must NEVER contain ambiguous "amount" key in serialized dictionary
    assert "amount" not in payment_dict

    # Raw JSON string verification
    assert '"amount_paise": 50000' in serialized_json or '"amount_paise":50000' in serialized_json
    assert '"amount_inr": 500.0' in serialized_json or '"amount_inr":500.0' in serialized_json
    assert '"amount_formatted": "₹500.00"' in serialized_json or '"amount_formatted":"₹500.00"' in serialized_json
    assert '"amount": 50000' not in serialized_json
    assert '"amount":50000' not in serialized_json


def test_canonical_integer_accounting_preserved():
    """Verify that canonical stored amount remains exact integer paise in code."""
    now_str = datetime.now(UTC).isoformat()

    # Initializing via amount_paise
    p1 = PaymentContext(
        payment_id="pay_1",
        amount_paise=50000,
        currency="INR",
        method="card",
        bank=None,
        card_network="Visa",
        international=False,
        order_id=None,
        error_code=None,
        error_reason=None,
        error_source=None,
        error_step=None,
        failed_at=now_str,
    )
    assert p1.amount == 50000
    assert p1.amount_paise == 50000
    assert isinstance(p1.amount, int)
    assert isinstance(p1.amount_paise, int)

    # Initializing via legacy amount keyword argument
    p2 = PaymentContext(
        payment_id="pay_2",
        amount=50000,
        currency="INR",
        method="card",
        bank=None,
        card_network="Visa",
        international=False,
        order_id=None,
        error_code=None,
        error_reason=None,
        error_source=None,
        error_step=None,
        failed_at=now_str,
    )
    assert p2.amount == 50000
    assert p2.amount_paise == 50000
    assert isinstance(p2.amount, int)
    assert isinstance(p2.amount_paise, int)
    assert p2.amount_inr == 500.00
    assert p2.amount_formatted == "₹500.00"


def test_gemini_prompt_contains_explicit_monetary_unit_convention():
    """Verify that Gemini prompt explicitly instructs the LLM on monetary units."""
    context = get_dummy_context()
    context.payment = PaymentContext(
        payment_id="pay_50000",
        amount_paise=50000,
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
        failed_at=datetime.now(UTC).isoformat(),
    )

    prompt = GeminiProvider.build_prompt(context)

    # Required prompt statements
    assert "Monetary Unit Convention:" in prompt
    assert "Razorpay amounts are stored in the smallest currency subunit." in prompt
    assert "For INR, 100 paise = ₹1." in prompt
    assert "amount_paise=50000 means ₹500.00 INR." in prompt
    assert "Use amount_inr for human-readable reasoning." in prompt
    assert "Never interpret amount_paise as rupees." in prompt

    # The context inside the prompt must have unambiguous amounts
    assert '"amount_paise": 50000' in prompt
    assert '"amount_inr": 500.0' in prompt
    assert '"amount_formatted": "₹500.00"' in prompt
    assert '"amount": 50000' not in prompt


@pytest.mark.asyncio
async def test_context_builder_money_integration(db_session, setup_test_data):
    """Verify that context_builder builds explicit money fields from real database records."""
    from sqlalchemy import select

    case = (
        await db_session.execute(
            select(RecoveryCase).where(RecoveryCase.status == "CREATED").limit(1)
        )
    ).scalar_one()

    # Ensure case payment amount is known (setup_test_data creates payment with amount=50000 paise)
    case.payment.amount = 50000
    await db_session.commit()

    context = await build_recovery_context(db_session, case)

    assert context.payment.amount_paise == 50000
    assert context.payment.amount_inr == 500.00
    assert context.payment.amount_formatted == "₹500.00"
    assert context.payment.amount == 50000
    assert context.payment.currency == "INR"

    dumped = context.payment.model_dump()
    assert dumped["amount_paise"] == 50000
    assert dumped["amount_inr"] == 500.00
    assert dumped["amount_formatted"] == "₹500.00"
    assert "amount" not in dumped
