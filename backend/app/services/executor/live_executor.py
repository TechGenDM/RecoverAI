"""Live recovery executor — calls real Razorpay Payment Links API."""

import logging

from app.services.executor.base import ExecutionResult, RecoveryExecutor
from app.services.razorpay_client import (
    PaymentLinkResult,
    RazorpayAPIError,
    RazorpayClient,
    RazorpayDuplicateReferenceError,
    RazorpayTimeoutError,
)

logger = logging.getLogger(__name__)


class LiveRecoveryExecutor(RecoveryExecutor):
    """Executes recovery actions via real Razorpay API calls.

    Requires an injected RazorpayClient instance.
    """

    def __init__(self, client: RazorpayClient) -> None:
        self._client = client

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
        """Create a Payment Link via Razorpay API."""
        try:
            result = await self._client.create_payment_link(
                amount=amount,
                currency=currency,
                reference_id=reference_id,
                expire_by=expire_by,
                description=description,
                customer=customer,
            )
            return ExecutionResult(
                success=True,
                razorpay_link_id=result.plink_id,
                short_url=result.short_url,
                expire_by=result.expire_by,
                reference_id=result.reference_id,
                raw=result.raw,
            )
        except RazorpayDuplicateReferenceError as exc:
            logger.warning(
                "Duplicate reference_id %s — link already exists (defensive fallback)",
                reference_id,
            )
            return ExecutionResult(
                definite_failure=True,
                reference_id=reference_id,
                error=f"DUPLICATE_REFERENCE: {exc.description}",
            )
        except RazorpayTimeoutError as exc:
            logger.warning("Razorpay timeout for %s: %s", reference_id, exc)
            return ExecutionResult(
                timeout=True,
                reference_id=reference_id,
                error=str(exc),
            )
        except RazorpayAPIError as exc:
            logger.error(
                "Razorpay API error for %s: %s", reference_id, exc
            )
            return ExecutionResult(
                definite_failure=True,
                reference_id=reference_id,
                error=f"{exc.error_code}: {exc.description}",
            )

    async def reconcile_by_id(self, plink_id: str) -> ExecutionResult:
        """Fetch a Payment Link by its Razorpay ID."""
        try:
            result = await self._client.fetch_payment_link(plink_id)
            return _link_to_result(result)
        except RazorpayTimeoutError as exc:
            return ExecutionResult(timeout=True, error=str(exc))
        except RazorpayAPIError as exc:
            return ExecutionResult(
                definite_failure=True,
                error=f"{exc.error_code}: {exc.description}",
            )

    async def reconcile_by_reference_id(
        self, reference_id: str
    ) -> list[ExecutionResult]:
        """Fetch Payment Links by reference_id."""
        try:
            results = await self._client.fetch_payment_links_by_reference_id(
                reference_id
            )
            return [_link_to_result(r) for r in results]
        except RazorpayTimeoutError as exc:
            # Return a single timeout result so caller knows GET failed
            return [ExecutionResult(timeout=True, error=str(exc))]
        except RazorpayAPIError as exc:
            return [
                ExecutionResult(
                    definite_failure=True,
                    error=f"{exc.error_code}: {exc.description}",
                )
            ]


def _link_to_result(link: PaymentLinkResult) -> ExecutionResult:
    """Convert a PaymentLinkResult to an ExecutionResult."""
    return ExecutionResult(
        success=True,
        razorpay_link_id=link.plink_id,
        short_url=link.short_url,
        expire_by=link.expire_by,
        reference_id=link.reference_id,
        raw=link.raw,
    )
