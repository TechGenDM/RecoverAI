"""Simulation service — deterministic batch evaluation of recovery scenarios.

Uses SHA-256 for all randomness, SIM_EPOCH for all timestamps.
SIMULATED mode NEVER invokes the Razorpay API or LIVE executor.
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditEvent, Payment, RecoveryAction, RecoveryCase
from app.services.analysis_service import analyze_and_decide
from app.services.recovery_service import run_execution_phase
from app.services.safety_validator import compute_heuristic_likelihood

logger = logging.getLogger(__name__)

# Base epoch for simulation clock — complete independence from wall-clock time
SIM_EPOCH = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)

# Core deterministic scenarios for indices 0-7
CORE_SCENARIOS = [
    {
        "reason": "insufficient_funds",
        "email": "sim+1@example.com",
        "phone": "9999999991",
        "amount": 150000,
    },
    {
        "reason": "invalid_otp",
        "email": "sim+2@example.com",
        "phone": "9999999992",
        "amount": 25000,
    },
    {
        "reason": "gateway_technical_error",
        "email": "sim+3@example.com",
        "phone": "9999999993",
        "amount": 500000,
    },
    {
        "reason": "card_declined",
        "email": "sim+4@example.com",
        "phone": "9999999994",
        "amount": 75000,
    },
    {
        "reason": "fraud_suspected",
        "email": "sim+5@example.com",
        "phone": "9999999995",
        "amount": 1000000,
    },
    {
        "reason": "network_error",
        "email": "sim+6@example.com",
        "phone": "9999999996",
        "amount": 12500,
    },
    {"reason": "insufficient_funds", "email": None, "phone": None, "amount": 200000},
    {
        "reason": "gateway_technical_error",
        "email": "sim+8@example.com",
        "phone": None,
        "amount": 350000,
    },
]

# Random scenario generation pool
REASONS = [
    "insufficient_funds",
    "invalid_otp",
    "gateway_technical_error",
    "card_declined",
    "fraud_suspected",
    "network_error",
]


def get_deterministic_hash(seed: int, scenario_index: int) -> float:
    """Returns a deterministic float between 0.0 and 1.0 using SHA-256.

    Identical across processes, machines, Python runs, and repeated calls.
    """
    key = f"sim_{seed}_{scenario_index}".encode()
    hex_digest = hashlib.sha256(key).hexdigest()
    return int(hex_digest[:8], 16) / 0xFFFFFFFF


def generate_scenario_data(seed: int, index: int) -> dict:
    """Generate deterministic scenario data for a given seed and index."""
    if index < len(CORE_SCENARIOS):
        return CORE_SCENARIOS[index]

    h = get_deterministic_hash(seed, index)

    # Pick reason
    reason_idx = int(h * 100) % len(REASONS)
    reason = REASONS[reason_idx]

    # Pick contact capability
    contact_h = get_deterministic_hash(seed, index + 10000)
    has_email = contact_h < 0.8
    has_phone = contact_h > 0.2

    # Amount between 5000 (50 INR) and 500000 (5000 INR)
    amount_h = get_deterministic_hash(seed, index + 20000)
    amount = 5000 + int(amount_h * 495000)

    return {
        "reason": reason,
        "email": f"sim+{index}@example.com" if has_email else None,
        "phone": f"99999999{index % 100:02d}" if has_phone else None,
        "amount": amount,
    }


async def run_simulation(session: AsyncSession, seed: int, scenario_count: int) -> dict:
    """Execute a batch of simulated scenarios idempotently."""
    logger.info("Starting simulation run with seed=%d, count=%d", seed, scenario_count)

    new_cases_processed = 0
    already_existed = 0

    # Process scenarios idempotently
    for index in range(scenario_count):
        payment_id = f"pay_sim_src_{seed}_{index}"

        stmt = select(Payment).where(Payment.razorpay_payment_id == payment_id)
        existing_payment = (await session.execute(stmt)).scalar_one_or_none()

        if existing_payment:
            already_existed += 1
            case_stmt = select(RecoveryCase).where(
                RecoveryCase.original_payment_id == payment_id
            )
            case = (await session.execute(case_stmt)).scalar_one_or_none()
            if not case:
                logger.error("Payment %s exists but no RecoveryCase found!", payment_id)
                continue

            # Reset ANALYSING back to CREATED for partial run recovery
            if case.status == "ANALYSING":
                case.status = "CREATED"
                await session.commit()
                # Continue processing
        else:
            # Create synthetic data
            scenario = generate_scenario_data(seed, index)

            h_time = get_deterministic_hash(seed, index + 30000)
            hours_ago = 1 + (h_time * 24)  # 1 to 25 hours ago
            failed_at = SIM_EPOCH - timedelta(hours=hours_ago)

            payment = Payment(
                razorpay_payment_id=payment_id,
                amount=scenario["amount"],
                currency="INR",
                status="failed",
                error_reason=scenario["reason"],
                error_source="customer"
                if scenario["reason"] in ["insufficient_funds", "invalid_otp"]
                else "gateway",
                error_step="payment_authentication",
                payload_snapshot={},
                failed_at=failed_at,
                created_at=failed_at,
            )
            session.add(payment)
            await session.flush()

            # Create Customer logic
            from app.models.customer import Customer

            customer = None
            if scenario["email"] or scenario["phone"]:
                customer_stmt = select(Customer).where(
                    and_(
                        Customer.email == scenario["email"],
                        Customer.phone == scenario["phone"],
                    )
                )
                customer = (await session.execute(customer_stmt)).scalars().first()
                if not customer:
                    customer = Customer(
                        email=scenario["email"],
                        phone=scenario["phone"],
                        name=f"Sim Customer {index}",
                    )
                    session.add(customer)
                    await session.flush()
                payment.customer_id = customer.id

            # Create Case
            window_expires = failed_at + timedelta(
                hours=settings.RECOVERY_MAX_WINDOW_HOURS
            )
            case = RecoveryCase(
                original_payment_id=payment.razorpay_payment_id,
                payment_fk=payment.id,
                customer_id=payment.customer_id,
                amount_at_risk=payment.amount,
                status="CREATED",
                mode="SIMULATED",
                recovery_window_expires_at=window_expires,
                attempt_count=0,
            )
            session.add(case)
            await session.commit()
            new_cases_processed += 1

        # Drive case through the pipeline if it's active
        await process_simulation_case(session, case.id, seed, index)

    return {
        "status": "completed",
        "seed": seed,
        "scenarios_requested": scenario_count,
        "new_cases_processed": new_cases_processed,
        "already_existed": already_existed,
    }


async def process_simulation_case(
    session: AsyncSession, case_id: str, seed: int, index: int
) -> None:
    """Drive a single case through M2, M3, and Outcome loop based on current status."""
    case = await session.get(RecoveryCase, case_id)
    if not case:
        return

    # Invariant: Simulation can only ever process SIMULATED cases
    if case.mode != "SIMULATED":
        raise ValueError(
            f"process_simulation_case can only execute SIMULATED cases, got mode={case.mode}"
        )

    if case.status in ["RECOVERED", "STOPPED", "ESCALATED"]:
        return  # Terminal

    if case.status in ["CREATED", "WAITING"]:
        # Only process WAITING if due_at is past the simulation evaluation_time
        if case.status == "WAITING" and case.due_at and case.due_at > SIM_EPOCH:
            return  # Still waiting in simulation time

        # Transition to ANALYSING — M2 owns attempt_count via analyze_and_decide
        case.status = "ANALYSING"
        case.attempt_count += 1
        await session.commit()

        # M2 Analysis
        try:
            await analyze_and_decide(session, case, now=SIM_EPOCH)
            await session.commit()
        except Exception as e:
            logger.error("Simulation M2 failed for case %s: %s", case.id, e)
            await session.rollback()
            raise

    # M3 Execution Phase — strictly scoped to this case_id and SIMULATED mode
    case = await session.get(RecoveryCase, case_id)
    if case.status in ("EXECUTING", "ANALYSING"):
        await run_execution_phase(
            session, now=SIM_EPOCH, case_id=case.id, mode="SIMULATED"
        )

    # Simulated Outcome Phase
    case = await session.get(RecoveryCase, case_id)
    if case.status == "LINK_SENT":
        await _apply_simulated_outcome(session, case, seed, index)


async def _apply_simulated_outcome(
    session: AsyncSession,
    case: RecoveryCase,
    seed: int,
    index: int,
) -> None:
    """Apply deterministic simulated outcome using heuristic recovery likelihood."""
    scenario = generate_scenario_data(seed, index)
    likelihood = compute_heuristic_likelihood(scenario["reason"])

    outcome_h = get_deterministic_hash(seed, index + 40000)
    is_recovered = outcome_h < likelihood

    # Get the action
    action_stmt = (
        select(RecoveryAction)
        .where(
            and_(
                RecoveryAction.case_id == case.id,
                RecoveryAction.status == "SUCCESS",
                RecoveryAction.action_type == "SEND_PAYMENT_LINK",
            )
        )
        .order_by(RecoveryAction.attempt_number.desc())
        .limit(1)
    )
    action = (await session.execute(action_stmt)).scalar_one_or_none()

    if is_recovered:
        recovered_payment_id = f"pay_sim_rec_{seed}_{index}"

        # Check if payment already exists (idempotency)
        pay_stmt = select(Payment).where(
            Payment.razorpay_payment_id == recovered_payment_id
        )
        existing_rec_payment = (await session.execute(pay_stmt)).scalar_one_or_none()

        if existing_rec_payment is None:
            # Create the recovered payment
            original_payment = await session.get(Payment, case.payment_fk)
            rec_payment = Payment(
                razorpay_payment_id=recovered_payment_id,
                customer_id=case.customer_id,
                amount=case.amount_at_risk,
                currency=original_payment.currency,
                status="captured",
                payload_snapshot={},
                created_at=SIM_EPOCH + timedelta(minutes=int(outcome_h * 60)),
            )
            session.add(rec_payment)

            case.status = "RECOVERED"
            case.amount_recovered = case.amount_at_risk
            case.recovered_payment_id = recovered_payment_id
            case.resolved_at = SIM_EPOCH

            if action:
                action.outcome = "RECOVERED"

            session.add(
                AuditEvent(
                    case_id=case.id,
                    action_id=action.id if action else None,
                    event_type="SIMULATED_RECOVERY_SUCCESS",
                    actor="simulator",
                    mode="SIMULATED",
                    payload={"recovered_payment_id": recovered_payment_id},
                )
            )
        else:
            # Fail-closed reconciliation: verify exact identity
            original_payment = await session.get(Payment, case.payment_fk)
            inconsistencies = []

            if existing_rec_payment.amount != case.amount_at_risk:
                inconsistencies.append(
                    f"amount mismatch: payment={existing_rec_payment.amount} case={case.amount_at_risk}"
                )
            if existing_rec_payment.currency != original_payment.currency:
                inconsistencies.append(
                    f"currency mismatch: payment={existing_rec_payment.currency} case={original_payment.currency}"
                )
            if existing_rec_payment.customer_id != case.customer_id:
                inconsistencies.append(
                    f"customer_id mismatch: payment={existing_rec_payment.customer_id} case={case.customer_id}"
                )
            if existing_rec_payment.razorpay_payment_id != recovered_payment_id:
                inconsistencies.append(
                    f"payment_id mismatch: {existing_rec_payment.razorpay_payment_id} != {recovered_payment_id}"
                )

            if inconsistencies:
                # Fail safely — do NOT mark RECOVERED
                logger.error(
                    "Recovered payment reconciliation FAILED for case %s: %s",
                    case.id,
                    "; ".join(inconsistencies),
                )
                case.status = "STOPPED"
                case.stop_reason = (
                    f"RECONCILIATION_INCONSISTENCY: {'; '.join(inconsistencies)}"
                )
                case.resolved_at = SIM_EPOCH
                session.add(
                    AuditEvent(
                        case_id=case.id,
                        action_id=action.id if action else None,
                        event_type="SIMULATED_RECONCILIATION_INCONSISTENCY",
                        actor="simulator",
                        mode="SIMULATED",
                        payload={
                            "inconsistencies": inconsistencies,
                            "recovered_payment_id": recovered_payment_id,
                        },
                    )
                )
            else:
                # All checks pass — reconcile to RECOVERED without duplicate payment/audit
                case.status = "RECOVERED"
                case.amount_recovered = case.amount_at_risk
                case.recovered_payment_id = recovered_payment_id
                case.resolved_at = SIM_EPOCH

                if action:
                    action.outcome = "RECOVERED"
    else:
        # Expired
        case.status = "STOPPED"
        case.stop_reason = "payment_link_expired"
        case.resolved_at = SIM_EPOCH

        if action:
            action.outcome = "EXPIRED"

        session.add(
            AuditEvent(
                case_id=case.id,
                action_id=action.id if action else None,
                event_type="SIMULATED_RECOVERY_EXPIRED",
                actor="simulator",
                mode="SIMULATED",
                payload={"reason": "payment_link_expired"},
            )
        )

    await session.commit()
