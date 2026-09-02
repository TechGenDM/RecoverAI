import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent, Customer, Payment, RecoveryCase, WebhookEvent

logger = logging.getLogger(__name__)

# Keys permitted in the minimized payload_snapshot
ALLOWED_PAYMENT_ENTITY_KEYS = {
    "id",
    "entity",
    "amount",
    "currency",
    "status",
    "order_id",
    "invoice_id",
    "international",
    "method",
    "amount_refunded",
    "refund_status",
    "captured",
    "description",
    "bank",
    "wallet",
    "vpa",
    "email",
    "contact",
    "customer_id",
    "error_code",
    "error_description",
    "error_source",
    "error_step",
    "error_reason",
    "created_at",
}

# Safe card keys if card dictionary is present
ALLOWED_CARD_KEYS = {
    "network",
    "type",
    "issuer",
    "last4",
    "international",
    "emi",
    "sub_type",
}

# Keys retained in the minimized webhook_events.payload
# Excludes nested payload.payment.entity (which may contain sensitive gateway data)
# and any account/merchant-level secrets.
ALLOWED_WEBHOOK_EVENT_KEYS = {
    "entity",
    "account_id",
    "event",
    "contains",
    "created_at",
}


def verify_razorpay_signature(
    raw_body: bytes, signature: str | None, secret: str
) -> bool:
    """Verifies HMAC-SHA256 signature using constant-time comparison.

    Never logs the webhook secret or provided signature.
    """
    if not signature or not secret:
        return False
    try:
        expected_signature = hmac.new(
            key=secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)
    except (TypeError, ValueError):
        return False


def sanitize_payment_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Deterministically extracts and sanitizes payment entity fields.

    Strips any raw card credentials, tokens, or unneeded sensitive gateway fields.
    """
    sanitized: dict[str, Any] = {}
    for key, val in entity.items():
        if key in ALLOWED_PAYMENT_ENTITY_KEYS:
            sanitized[key] = val

    # If card metadata exists, extract only non-sensitive network/type info
    card = entity.get("card")
    if isinstance(card, dict):
        sanitized["card"] = {k: v for k, v in card.items() if k in ALLOWED_CARD_KEYS}

    return sanitized


def sanitize_webhook_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Creates a minimized/redacted representation of the webhook payload.

    Only retains event envelope metadata. The nested payment entity is stored
    separately in the sanitized Payment.payload_snapshot.
    """
    minimized: dict[str, Any] = {}
    for key, val in payload.items():
        if key in ALLOWED_WEBHOOK_EVENT_KEYS:
            minimized[key] = val

    # Include only the payment ID from nested payload for traceability
    nested_payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    if isinstance(nested_payment, dict) and "id" in nested_payment:
        minimized["_payment_id"] = nested_payment["id"]

    return minimized


def parse_timestamp(ts: Any) -> datetime | None:
    """Converts a Unix epoch timestamp or ISO string to UTC datetime.

    Returns None if the timestamp is missing, malformed, or not a recognized type.
    Callers must handle None explicitly — no silent fabrication of failure time.
    """
    if isinstance(ts, (int, float)):
        if ts <= 0:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


class WebhookIngestionService:
    @staticmethod
    async def _upsert_customer(
        db: AsyncSession,
        customer_id_str: str,
        email: str | None,
        contact: str | None,
    ) -> Customer:
        """Concurrency-safe customer upsert using ON CONFLICT DO NOTHING + SELECT.

        Handles the race where two concurrent webhooks for the same customer_id
        both try to INSERT simultaneously.
        """
        # Attempt INSERT with ON CONFLICT DO NOTHING
        stmt = (
            insert(Customer)
            .values(
                razorpay_customer_id=customer_id_str,
                email=email,
                phone=contact,
                name=None,
            )
            .on_conflict_do_nothing(index_elements=["razorpay_customer_id"])
            .returning(Customer.id)
        )
        result = await db.execute(stmt)
        new_id = result.scalar_one_or_none()

        if new_id is not None:
            # We inserted successfully; flush to ensure ID is available
            await db.flush()
            # Re-fetch the full ORM object
            cust_result = await db.execute(
                select(Customer).where(Customer.id == new_id)
            )
            return cust_result.scalar_one()

        # Another transaction already created this customer; SELECT it
        cust_result = await db.execute(
            select(Customer).where(Customer.razorpay_customer_id == customer_id_str)
        )
        customer = cust_result.scalar_one()

        # Update contact info if provided and currently empty
        if email and not customer.email:
            customer.email = email
        if contact and not customer.phone:
            customer.phone = contact

        return customer

    @staticmethod
    async def process_webhook(
        event_id: str,
        payload: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[int, dict[str, Any]]:
        """Processes an incoming validated Razorpay webhook event transactionally.

        Guarantees:
        1. Atomically deduplicates event_id via PostgreSQL unique constraint.
        2. Idempotently ignores duplicate deliveries (returns HTTP 200).
        3. Persists customer, payment, recovery_case, and audit_event in one atomic transaction.
        4. Never calls external APIs, LLMs, or executors.
        5. Preserves invariant: one original payment -> at most one RecoveryCase.
        6. Stores only minimized/redacted data in webhook_events.payload.
        7. Rejects malformed timestamps, invalid amounts, and unexpected statuses.
        """
        event_type = payload.get("event")
        if not event_type:
            return 400, {"error": "missing_event_type"}

        # 1. Minimize webhook payload before persisting (data-minimization policy)
        minimized_payload = sanitize_webhook_payload(payload)

        # 2. Atomic deduplication of webhook event ID using ON CONFLICT DO NOTHING
        stmt = (
            insert(WebhookEvent)
            .values(
                razorpay_event_id=event_id,
                event_type=event_type,
                payload=minimized_payload,
                signature_verified=True,
                processed=False,
            )
            .on_conflict_do_nothing(index_elements=["razorpay_event_id"])
            .returning(WebhookEvent.id)
        )
        result = await db.execute(stmt)
        webhook_event_id = result.scalar_one_or_none()

        if webhook_event_id is None:
            # Duplicate webhook delivery detected; return 200 OK immediately
            return 200, {
                "status": "duplicate_ignored",
                "message": f"Webhook event {event_id} already ingested",
                "event_id": event_id,
            }

        # 3. Check event type support
        if event_type != "payment.failed":
            # Record that this unsupported event was received and mark processed
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {
                "status": "ignored",
                "reason": f"Event type '{event_type}' not handled in M1",
                "event_id": event_id,
            }

        # 4. Extract and validate payment entity
        payment_payload = payload.get("payload", {}).get("payment", {})
        entity = payment_payload.get("entity", {})
        if not entity or not isinstance(entity, dict):
            # Mark processing error on webhook event
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = "Missing payment entity in payload"
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "missing_payment_entity"}

        razorpay_payment_id = entity.get("id")
        if not razorpay_payment_id:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = "Missing payment id in entity"
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "missing_payment_id"}

        # 5. Validate payment amount: must be a positive integer
        raw_amount = entity.get("amount")
        if not isinstance(raw_amount, int) or raw_amount <= 0:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = (
                    f"Invalid payment amount: {raw_amount!r} "
                    "(must be a positive integer in paise)"
                )
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "invalid_payment_amount"}

        # 6. Validate payment entity status == "failed"
        entity_status = entity.get("status")
        if entity_status != "failed":
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = (
                    f"Unexpected payment status '{entity_status}' "
                    "for payment.failed event"
                )
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "invalid_payment_status"}

        # 7. Validate timestamp — reject malformed/missing created_at
        raw_ts = entity.get("created_at") or payload.get("created_at")
        failed_at = parse_timestamp(raw_ts)
        if failed_at is None:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = (
                    f"Missing or malformed payment created_at: {raw_ts!r}"
                )
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "invalid_timestamp"}

        # 8. Check if payment already exists in database (idempotency for same payment_id)
        existing_payment_result = await db.execute(
            select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id)
        )
        existing_payment = existing_payment_result.scalar_one_or_none()

        if existing_payment:
            # Payment record already exists. Check if recovery case exists.
            existing_case_result = await db.execute(
                select(RecoveryCase).where(
                    RecoveryCase.original_payment_id == razorpay_payment_id
                )
            )
            existing_case = existing_case_result.scalar_one_or_none()

            # Mark webhook event processed
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()

            return 200, {
                "status": "payment_already_recorded",
                "payment_id": razorpay_payment_id,
                "case_id": str(existing_case.id) if existing_case else None,
                "event_id": event_id,
            }

        # 9. Concurrency-safe customer upsert if razorpay_customer_id is present
        customer_id_str = entity.get("customer_id")
        email = entity.get("email")
        contact = entity.get("contact")
        customer_record: Customer | None = None

        if customer_id_str:
            customer_record = await WebhookIngestionService._upsert_customer(
                db, customer_id_str, email, contact
            )

        # 10. Sanitize payload snapshot
        sanitized_entity = sanitize_payment_entity(entity)

        # Extract payment method details
        method = entity.get("method")
        bank = entity.get("bank")
        card = entity.get("card") or {}
        card_network = card.get("network") if isinstance(card, dict) else None
        vpa = entity.get("vpa")

        # 11. Create Payment record — handle concurrent unique constraint violation
        payment = Payment(
            razorpay_payment_id=razorpay_payment_id,
            customer_id=customer_record.id if customer_record else None,
            amount=raw_amount,
            currency=entity.get("currency", "INR"),
            status="failed",
            error_code=entity.get("error_code"),
            error_description=entity.get("error_description"),
            error_source=entity.get("error_source"),
            error_step=entity.get("error_step"),
            error_reason=entity.get("error_reason"),
            method=method,
            bank=bank,
            card_network=card_network,
            vpa=vpa,
            international=bool(entity.get("international", False)),
            order_id=entity.get("order_id"),
            payload_snapshot=sanitized_entity,
            failed_at=failed_at,
        )
        db.add(payment)

        try:
            await db.flush()  # Generate payment.id; may raise IntegrityError
        except IntegrityError:
            # Concurrent insertion of same razorpay_payment_id — another
            # transaction won the race. Rollback and return idempotent response.
            await db.rollback()

            # Re-open a clean session state to read existing records
            existing_payment_result = await db.execute(
                select(Payment).where(
                    Payment.razorpay_payment_id == razorpay_payment_id
                )
            )
            existing_payment = existing_payment_result.scalar_one_or_none()
            existing_case_result = await db.execute(
                select(RecoveryCase).where(
                    RecoveryCase.original_payment_id == razorpay_payment_id
                )
            )
            existing_case = existing_case_result.scalar_one_or_none()

            return 200, {
                "status": "payment_already_recorded",
                "payment_id": razorpay_payment_id,
                "case_id": str(existing_case.id) if existing_case else None,
                "event_id": event_id,
            }

        # 12. Create RecoveryCase record with status CREATED
        recovery_window_expires_at = failed_at + timedelta(
            hours=settings.RECOVERY_MAX_WINDOW_HOURS
        )

        recovery_case = RecoveryCase(
            original_payment_id=razorpay_payment_id,
            payment_fk=payment.id,
            customer_id=customer_record.id if customer_record else None,
            status="CREATED",
            mode=settings.MODE,
            attempt_count=0,
            amount_at_risk=payment.amount,
            amount_recovered=0,
            recovered_payment_id=None,
            recovery_window_expires_at=recovery_window_expires_at,
            due_at=None,
            resolved_at=None,
            stop_reason=None,
        )
        db.add(recovery_case)
        await db.flush()  # Generate recovery_case.id for audit event

        # 13. Create AuditEvent record
        audit_payload = {
            "payment_id": razorpay_payment_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "error_code": payment.error_code,
            "error_reason": payment.error_reason,
            "recovery_window_expires_at": recovery_window_expires_at.isoformat(),
            "event_id": event_id,
        }

        audit_event = AuditEvent(
            case_id=recovery_case.id,
            action_id=None,
            event_type="PAYMENT_FAILED_INGESTED",
            actor="webhook",
            mode=recovery_case.mode,
            payload=audit_payload,
        )
        db.add(audit_event)

        # 14. Mark webhook_event as processed
        event_obj = await db.get(WebhookEvent, webhook_event_id)
        if event_obj:
            event_obj.processed = True
            event_obj.processed_at = datetime.now(UTC)

        # 15. Commit transaction atomically
        await db.commit()

        return 200, {
            "status": "created",
            "message": "Payment failure ingested and recovery case created",
            "event_id": event_id,
            "payment_id": razorpay_payment_id,
            "case_id": str(recovery_case.id),
        }
