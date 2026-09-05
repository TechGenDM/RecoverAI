import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent, Customer, Payment, RecoveryCase, WebhookEvent
from app.models.recovery_action import RecoveryAction

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
    def _normalize_contact_info(
        email: Any, contact: Any
    ) -> tuple[str | None, str | None]:
        """Validates and normalizes email and phone contact info.

        Rejects empty strings, whitespace, and malformed values.
        """
        norm_email: str | None = None
        if isinstance(email, str):
            cleaned_email = email.strip()
            if (
                cleaned_email
                and " " not in cleaned_email
                and cleaned_email.count("@") == 1
                and "." in cleaned_email.split("@")[1]
                and len(cleaned_email) >= 5
            ):
                norm_email = cleaned_email.lower()

        norm_phone: str | None = None
        if isinstance(contact, str):
            cleaned_phone = contact.strip()
            digits = [c for c in cleaned_phone if c.isdigit()]
            if 7 <= len(digits) <= 15 and re.fullmatch(
                r"^\+?[\d\s\-()]{7,25}$", cleaned_phone
            ):
                norm_phone = cleaned_phone

        return norm_email, norm_phone

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
    async def _find_or_create_fallback_customer(
        db: AsyncSession,
        email: Any,
        contact: Any,
    ) -> Customer | None:
        """Finds or creates an internal Customer when razorpay_customer_id is absent.

        Uses verified contact info (email/phone) from the payment payload.
        Idempotent and concurrency-safe via advisory lock when on PostgreSQL.
        Does NOT fabricate a Razorpay customer ID or call Razorpay APIs.
        """
        norm_email, norm_phone = WebhookIngestionService._normalize_contact_info(
            email, contact
        )
        if not norm_email and not norm_phone:
            return None

        # Advisory lock on PostgreSQL for concurrency safety
        try:
            bind = db.bind or (
                getattr(db, "sync_session", None) and db.sync_session.bind
            )
            dialect_name = getattr(bind.dialect, "name", "") if bind else ""
            if dialect_name == "postgresql":
                lock_key = f"cust_fallback:{norm_email or norm_phone}"
                await db.execute(
                    select(func.pg_advisory_xact_lock(func.hashtext(lock_key)))
                )
        except (SQLAlchemyError, DBAPIError) as exc:
            logger.debug("Advisory lock skipped or unavailable: %s", exc)

        # 1. Search for existing Customer matching email or phone
        conditions = []
        if norm_email:
            conditions.append(Customer.email == norm_email)
        if norm_phone:
            conditions.append(Customer.phone == norm_phone)

        stmt = (
            select(Customer).where(or_(*conditions)).order_by(Customer.created_at.asc())
        )
        result = await db.execute(stmt)
        customer = result.scalars().first()

        if customer is not None:
            updated = False
            if norm_email and not customer.email:
                customer.email = norm_email
                updated = True
            if norm_phone and not customer.phone:
                customer.phone = norm_phone
                updated = True
            if updated:
                await db.flush()
            return customer

        # 2. Create new internal Customer without a razorpay_customer_id
        customer = Customer(
            razorpay_customer_id=None,
            email=norm_email,
            phone=norm_phone,
            name=None,
        )
        db.add(customer)
        await db.flush()
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
            await db.rollback()
            return 200, {
                "status": "duplicate_ignored",
                "message": f"Webhook event {event_id} already ingested",
                "event_id": event_id,
            }

        # 3. Event type dispatch
        if event_type == "payment_link.paid":
            return await WebhookIngestionService._handle_payment_link_paid(
                db, webhook_event_id, event_id, payload
            )

        if event_type in ("payment_link.expired", "payment_link.cancelled"):
            return await WebhookIngestionService._handle_payment_link_terminal(
                db, webhook_event_id, event_id, payload, event_type
            )

        if event_type != "payment.failed":
            # Unsupported event type — mark processed and ignore
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {
                "status": "ignored",
                "reason": f"Event type '{event_type}' not handled",
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

        # 9. Concurrency-safe customer association:
        # Path A (existing): If razorpay_customer_id is present, use _upsert_customer
        # Path B (fallback): If customer_id is absent, find or create internal Customer via email/contact
        customer_id_str = entity.get("customer_id")
        email = entity.get("email")
        contact = entity.get("contact")
        customer_record: Customer | None = None

        if customer_id_str:
            customer_record = await WebhookIngestionService._upsert_customer(
                db, customer_id_str, email, contact
            )
        else:
            customer_record = (
                await WebhookIngestionService._find_or_create_fallback_customer(
                    db, email, contact
                )
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

    # ── M3 Webhook Handlers ──────────────────────────────────────────

    @staticmethod
    async def _handle_payment_link_paid(
        db: AsyncSession,
        webhook_event_id: Any,
        event_id: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Handle payment_link.paid webhook.

        Verified payload paths (from official Razorpay docs):
        - payload.payment_link.entity.reference_id
        - payload.payment_link.entity.id (plink_*)
        - payload.payment.entity.id (pay_*)
        - payload.payment.entity.amount (paise)
        - payload.payment.entity.status ("captured")
        - payload.payment.entity.created_at (Unix timestamp)
        """
        # Extract payment_link entity
        plink_entity = (
            payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        )
        reference_id = plink_entity.get("reference_id")
        if not reference_id:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "no_reference_id"}

        # Extract payment entity
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        new_payment_id = payment_entity.get("id")
        actual_amount = payment_entity.get("amount")
        payment_status = payment_entity.get("status")
        occurrence_ts_raw = payment_entity.get("created_at")

        if not new_payment_id or not isinstance(actual_amount, int):
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processing_error = (
                    "Invalid payment entity in payment_link.paid"
                )
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 400, {"error": "invalid_payment_entity"}

        occurrence_time = parse_timestamp(occurrence_ts_raw)

        # Look up RecoveryAction by reference_id
        action_result = await db.execute(
            select(RecoveryAction).where(
                RecoveryAction.razorpay_link_reference_id == reference_id
            )
        )
        action = action_result.scalar_one_or_none()

        if action is None:
            # Not our link — ignore gracefully
            logger.info(
                "payment_link.paid: no RecoveryAction for reference_id %s",
                reference_id,
            )
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "unknown_reference_id"}

        # Load case
        case = await db.get(RecoveryCase, action.case_id)
        if case is None:
            logger.error("Case %s not found for action %s", action.case_id, action.id)
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "case_not_found"}

        # Guard: check case status
        if case.status == "RECOVERED":
            # Already recovered — idempotent
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "already_recovered", "case_id": str(case.id)}

        if case.status not in ("LINK_SENT", "EXECUTING", "STOPPED"):
            logger.warning(
                "payment_link.paid for case %s in unexpected status %s",
                case.id,
                case.status,
            )
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "unexpected_case_status"}

        # Validate payment occurred inside recovery window
        # Payment occurrence time determines eligibility, NOT webhook arrival
        if occurrence_time is not None:
            if occurrence_time > case.recovery_window_expires_at:
                # Out-of-window payment — do NOT recover
                db.add(
                    AuditEvent(
                        case_id=case.id,
                        action_id=action.id,
                        event_type="OUT_OF_WINDOW_PAYMENT",
                        actor="webhook",
                        mode=case.mode,
                        payload={
                            "payment_id": new_payment_id,
                            "occurrence_time": occurrence_time.isoformat(),
                            "window_expires_at": case.recovery_window_expires_at.isoformat(),
                        },
                    )
                )
                event_obj = await db.get(WebhookEvent, webhook_event_id)
                if event_obj:
                    event_obj.processed = True
                    event_obj.processed_at = datetime.now(UTC)
                await db.commit()
                return 200, {
                    "status": "out_of_window",
                    "reason": "Payment occurred after recovery window",
                }

            # STOPPED case: only allow resurrection if payment inside window
            if case.status == "STOPPED":
                logger.info(
                    "Late webhook: STOPPED case %s, payment inside window — allowing recovery",
                    case.id,
                )

        # Validate exact amount match
        expected_amount = case.amount_at_risk
        if actual_amount != expected_amount:
            # Amount mismatch — DO NOT credit as recovery revenue
            # Persist the new payment for traceability
            recovered_payment = Payment(
                razorpay_payment_id=new_payment_id,
                customer_id=case.customer_id,
                amount=actual_amount,
                currency=plink_entity.get("currency", "INR"),
                status=payment_status or "captured",
                payload_snapshot=sanitize_payment_entity(payment_entity),
            )
            db.add(recovered_payment)

            case.status = "STOPPED"
            case.stop_reason = (
                f"Amount mismatch: expected={expected_amount}, actual={actual_amount}"
            )
            case.resolved_at = datetime.now(UTC)
            action.outcome = "FAILED"
            action.outcome_observed_at = datetime.now(UTC)

            db.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id,
                    event_type="RECOVERY_AMOUNT_MISMATCH",
                    actor="webhook",
                    mode=case.mode,
                    payload={
                        "expected": expected_amount,
                        "actual": actual_amount,
                        "new_payment_id": new_payment_id,
                    },
                )
            )
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {
                "status": "amount_mismatch",
                "expected": expected_amount,
                "actual": actual_amount,
            }

        # ── Valid recovery ──
        # Create NEW Payment record for the recovered payment
        recovered_payment = Payment(
            razorpay_payment_id=new_payment_id,
            customer_id=case.customer_id,
            amount=actual_amount,
            currency=plink_entity.get("currency", "INR"),
            status=payment_status or "captured",
            payload_snapshot=sanitize_payment_entity(payment_entity),
        )
        db.add(recovered_payment)

        # Update case to RECOVERED
        case.status = "RECOVERED"
        case.recovered_payment_id = new_payment_id
        case.amount_recovered = actual_amount
        # Use payment occurrence time, not webhook arrival time
        case.resolved_at = occurrence_time or datetime.now(UTC)

        # Update action outcome
        action.outcome = "RECOVERED"
        action.outcome_observed_at = datetime.now(UTC)

        # Audit events
        db.add(
            AuditEvent(
                case_id=case.id,
                action_id=action.id,
                event_type="RECOVERY_PAYMENT_RECEIVED",
                actor="webhook",
                mode=case.mode,
                payload={
                    "new_payment_id": new_payment_id,
                    "amount": actual_amount,
                    "payment_status": payment_status,
                    "occurrence_time": (
                        occurrence_time.isoformat() if occurrence_time else None
                    ),
                },
            )
        )
        db.add(
            AuditEvent(
                case_id=case.id,
                action_id=action.id,
                event_type="RECOVERY_CASE_RECOVERED",
                actor="webhook",
                mode=case.mode,
                payload={
                    "recovered_payment_id": new_payment_id,
                    "amount_recovered": actual_amount,
                },
            )
        )

        event_obj = await db.get(WebhookEvent, webhook_event_id)
        if event_obj:
            event_obj.processed = True
            event_obj.processed_at = datetime.now(UTC)

        await db.commit()
        return 200, {
            "status": "recovered",
            "case_id": str(case.id),
            "recovered_payment_id": new_payment_id,
            "amount_recovered": actual_amount,
        }

    @staticmethod
    async def _handle_payment_link_terminal(
        db: AsyncSession,
        webhook_event_id: Any,
        event_id: str,
        payload: dict[str, Any],
        event_type: str,
    ) -> tuple[int, dict[str, Any]]:
        """Handle payment_link.expired and payment_link.cancelled webhooks.

        Verified payload paths:
        - payload.payment_link.entity.reference_id
        - payload.payment_link.entity.id (plink_*)
        - payload.payment_link.entity.status ("expired"/"cancelled")
        Note: No payment entity in these events.
        """
        plink_entity = (
            payload.get("payload", {}).get("payment_link", {}).get("entity", {})
        )
        reference_id = plink_entity.get("reference_id")
        if not reference_id:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "no_reference_id"}

        # Look up RecoveryAction
        action_result = await db.execute(
            select(RecoveryAction).where(
                RecoveryAction.razorpay_link_reference_id == reference_id
            )
        )
        action = action_result.scalar_one_or_none()

        if action is None:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "unknown_reference_id"}

        case = await db.get(RecoveryCase, action.case_id)
        if case is None:
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "ignored", "reason": "case_not_found"}

        # Guard: don't downgrade terminal states
        if case.status in ("RECOVERED", "STOPPED", "ESCALATED"):
            event_obj = await db.get(WebhookEvent, webhook_event_id)
            if event_obj:
                event_obj.processed = True
                event_obj.processed_at = datetime.now(UTC)
            await db.commit()
            return 200, {"status": "already_terminal", "case_status": case.status}

        # Determine outcome from event_type
        is_expired = event_type == "payment_link.expired"
        outcome = "EXPIRED" if is_expired else "CANCELLED"
        stop_reason = "Payment link expired" if is_expired else "Payment link cancelled"
        audit_event_type = (
            "PAYMENT_LINK_EXPIRED" if is_expired else "PAYMENT_LINK_CANCELLED"
        )

        action.outcome = outcome
        action.outcome_observed_at = datetime.now(UTC)
        case.status = "STOPPED"
        case.stop_reason = stop_reason
        case.resolved_at = datetime.now(UTC)

        db.add(
            AuditEvent(
                case_id=case.id,
                action_id=action.id,
                event_type=audit_event_type,
                actor="webhook",
                mode=case.mode,
                payload={
                    "plink_id": plink_entity.get("id"),
                    "reference_id": reference_id,
                    "link_status": plink_entity.get("status"),
                },
            )
        )

        event_obj = await db.get(WebhookEvent, webhook_event_id)
        if event_obj:
            event_obj.processed = True
            event_obj.processed_at = datetime.now(UTC)

        await db.commit()
        return 200, {
            "status": "stopped",
            "case_id": str(case.id),
            "outcome": outcome,
        }
