"""Simulated recovery executor — zero external dependencies.

Returns deterministic fake results for SIMULATED mode.
This class MUST NOT import or reference RazorpayClient.
"""

import logging

from app.services.executor.base import ExecutionResult, RecoveryExecutor

logger = logging.getLogger(__name__)


class SimulatedRecoveryExecutor(RecoveryExecutor):
    """Executes recovery actions with deterministic fake results.

    Has ZERO external dependencies — no RazorpayClient, no httpx,
    no network calls. Pure computation only.
    """

    def __init__(self) -> None:
        # Intentionally takes no arguments — no client dependency
        pass

    async def execute(
        self,
        *,
        amount: int,
        currency: str,
        reference_id: str,
        expire_by: int,
        description: str,
        customer: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Return a deterministic fake Payment Link result."""
        logger.info(
            "SIMULATED: Creating payment link ref=%s amount=%d",
            reference_id,
            amount,
        )
        return ExecutionResult(
            success=True,
            razorpay_link_id=f"plink_sim_{reference_id}",
            short_url=f"https://rzp.io/sim/{reference_id}",
            expire_by=expire_by,
            reference_id=reference_id,
            raw={
                "id": f"plink_sim_{reference_id}",
                "short_url": f"https://rzp.io/sim/{reference_id}",
                "status": "created",
                "reference_id": reference_id,
                "expire_by": expire_by,
                "amount": amount,
                "currency": currency,
            },
        )

    async def reconcile_by_id(self, plink_id: str) -> ExecutionResult:
        """Return a deterministic fake reconciliation result."""
        return ExecutionResult(
            success=True,
            razorpay_link_id=plink_id,
            short_url=f"https://rzp.io/sim/{plink_id}",
            expire_by=0,
            reference_id="",
        )

    async def reconcile_by_reference_id(
        self, reference_id: str
    ) -> list[ExecutionResult]:
        """Return empty list — simulated links don't persist externally."""
        return []
