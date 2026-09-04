import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import AuditEvent, Payment, RecoveryCase

from .analysis_service import analyze_and_decide

logger = logging.getLogger(__name__)

async def claim_batch(session: AsyncSession, batch_size: int) -> list[str]:
    """
    Phase 1: Claim cases for processing using FOR UPDATE SKIP LOCKED.
    Returns list of claimed case IDs.
    """
    now = datetime.now(UTC)
    
    # Target CREATED cases, or WAITING cases whose due_at has passed
    stmt = (
        select(RecoveryCase.id)
        .where(
            or_(
                RecoveryCase.status == "CREATED",
                and_(
                    RecoveryCase.status == "WAITING",
                    RecoveryCase.due_at <= now
                )
            )
        )
        .order_by(RecoveryCase.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    
    case_ids = (await session.execute(stmt)).scalars().all()
    if not case_ids:
        return []
        
    # Mark them as ANALYSING
    update_stmt = (
        update(RecoveryCase)
        .where(RecoveryCase.id.in_(case_ids))
        .values(
            status="ANALYSING",
            due_at=None,
            updated_at=func.now()
        )
    )
    await session.execute(update_stmt)
    
    # Audit log the claim
    for cid in case_ids:
        audit = AuditEvent(
            case_id=cid,
            event_type="SCHEDULER_CLAIMED",
            actor="system",
            mode="LIVE",
            payload={"description": "Scheduler claimed case for LLM analysis"}
        )
        session.add(audit)
        
    await session.commit()
    return [str(cid) for cid in case_ids]

async def process_case(case_id: str) -> None:
    """
    Phase 2: Process a single case out-of-band of the claim lock.
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Fetch case fresh with its payment and customer
            stmt = (
                select(RecoveryCase)
                .options(
                    selectinload(RecoveryCase.payment).selectinload(Payment.customer)
                )
                .where(RecoveryCase.id == case_id)
            )
            case = (await session.execute(stmt)).scalar_one_or_none()
            
            if not case:
                logger.error(f"Case {case_id} not found during processing")
                return
                
            if case.status != "ANALYSING":
                logger.warning(f"Case {case_id} is no longer in ANALYSING state (stale claim)")
                return
                
            # 2. Analyze (includes LLM call and safety validation)
            try:
                await analyze_and_decide(session, case)
            except Exception as e:
                logger.exception(f"Error during analysis of {case_id}")
                # Fallback to waiting/retry or escalate
                case.status = "WAITING"
                case.due_at = datetime.now(UTC) + timedelta(hours=1)
                audit = AuditEvent(
                    case_id=case.id,
                    event_type="ANALYSIS_FAILED",
                    actor="system",
                    mode=case.mode,
                    payload={"error": str(e)}
                )
                session.add(audit)
                
            # 3. Commit the decision and state transition
            await session.commit()
    except Exception:
        logger.exception(f"Fatal error processing case {case_id}")

async def run_analysis_phase() -> int:
    """M2: Claim and analyze cases via LLM.

    Returns number of cases processed.
    """
    async with AsyncSessionLocal() as session:
        case_ids = await claim_batch(session, settings.SCHEDULER_BATCH_SIZE)

    if not case_ids:
        return 0

    tasks = [process_case(cid) for cid in case_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    return len(case_ids)


async def run_scheduler_tick() -> int:
    """Main entrypoint for the scheduler tick.

    Phase 1 (M2): Claim CREATED/WAITING cases → ANALYSING → LLM analysis.
    Phase 2 (M3): Execute pending SEND_PAYMENT_LINK decisions.

    Returns total number of actions processed.
    """
    # Avoid circular import; recovery_service depends on executor, not LLM
    from app.services.recovery_service import run_execution_phase

    # Phase 1: M2 Analysis
    analysis_count = await run_analysis_phase()

    # Phase 2: M3 Execution (logically separate)
    execution_count = 0
    try:
        async with AsyncSessionLocal() as session:
            execution_count = await run_execution_phase(session)
    except Exception:
        logger.exception("M3 execution phase failed")

    total = analysis_count + execution_count
    if total > 0:
        logger.info(
            "Scheduler tick: %d analysis + %d execution = %d total",
            analysis_count,
            execution_count,
            total,
        )
    return total
