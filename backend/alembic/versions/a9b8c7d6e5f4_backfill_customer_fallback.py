"""Backfill customer fallback for existing payments without customer_id

Revision ID: a9b8c7d6e5f4
Revises: 847924ad5257
Create Date: 2026-09-05 21:30:00.000000

"""

import json
import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "847924ad5257"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_contact(email, contact):
    norm_email = None
    if isinstance(email, str):
        cleaned = email.strip()
        if (
            cleaned
            and " " not in cleaned
            and cleaned.count("@") == 1
            and "." in cleaned.split("@")[1]
            and len(cleaned) >= 5
        ):
            norm_email = cleaned.lower()

    norm_phone = None
    if isinstance(contact, str):
        cleaned = contact.strip()
        digits = [c for c in cleaned if c.isdigit()]
        if 7 <= len(digits) <= 15 and re.fullmatch(r"^\+?[\d\s\-()]{7,25}$", cleaned):
            norm_phone = cleaned

    return norm_email, norm_phone


def upgrade() -> None:
    conn = op.get_bind()
    # Find all payments where customer_id is NULL
    rows = conn.execute(
        sa.text("SELECT id, payload_snapshot FROM payments WHERE customer_id IS NULL")
    ).fetchall()

    for pay_id, payload_raw in rows:
        payload = payload_raw
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                payload = None
        if not isinstance(payload, dict):
            continue

        email = payload.get("email")
        contact = payload.get("contact")
        norm_email, norm_phone = _normalize_contact(email, contact)
        if not norm_email and not norm_phone:
            continue

        # Look for existing customer
        cust_id = None
        if norm_email:
            existing = conn.execute(
                sa.text("SELECT id FROM customers WHERE email = :e"), {"e": norm_email}
            ).fetchone()
            if existing:
                cust_id = existing[0]

        if not cust_id and norm_phone:
            existing = conn.execute(
                sa.text("SELECT id FROM customers WHERE phone = :p"), {"p": norm_phone}
            ).fetchone()
            if existing:
                cust_id = existing[0]

        if not cust_id:
            cust_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    "INSERT INTO customers (id, razorpay_customer_id, email, phone, name, created_at, updated_at) "
                    "VALUES (:id, NULL, :email, :phone, NULL, NOW(), NOW())"
                ),
                {"id": cust_id, "email": norm_email, "phone": norm_phone},
            )

        # Update payment
        conn.execute(
            sa.text("UPDATE payments SET customer_id = :cid WHERE id = :pid"),
            {"cid": cust_id, "pid": pay_id},
        )

        # Update recovery_case (without changing status!)
        conn.execute(
            sa.text(
                "UPDATE recovery_cases SET customer_id = :cid WHERE payment_fk = :pid AND customer_id IS NULL"
            ),
            {"cid": cust_id, "pid": pay_id},
        )


def downgrade() -> None:
    pass
