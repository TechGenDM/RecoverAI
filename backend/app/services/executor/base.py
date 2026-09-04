"""Recovery Executor abstraction and result types."""

import abc
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    """Result of a recovery execution attempt.

    Attributes:
        success: Whether the execution definitely succeeded.
        definite_failure: Whether the execution definitively failed (4xx).
        timeout: Whether the outcome is unknown (timeout/network error).
        razorpay_link_id: The Razorpay plink_* identifier (if known).
        short_url: The short URL for the payment link (if known).
        expire_by: Unix timestamp when the link expires (if known).
        reference_id: The deterministic reference_id used.
        error: Error description if failed or timed out.
        raw: Raw response data from the API.
    """

    success: bool = False
    definite_failure: bool = False
    timeout: bool = False
    razorpay_link_id: str | None = None
    short_url: str | None = None
    expire_by: int | None = None
    reference_id: str = ""
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class RecoveryExecutor(abc.ABC):
    """Abstract base class for recovery execution.

    Subclasses implement the actual external action (live or simulated).
    Executors are responsible ONLY for the external call. They do NOT
    manage state transitions, database persistence, or audit events.
    """

    @abc.abstractmethod
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
        """Execute the recovery action (create Payment Link).

        Args:
            amount: Amount in smallest currency unit (paise).
            currency: Currency code (e.g. "INR").
            reference_id: Deterministic reference ID for idempotency.
            expire_by: Unix timestamp for link expiry.
            description: Payment link description.
            customer: Optional customer details (email, phone, name).

        Returns:
            ExecutionResult with success/failure/timeout status.
        """
        ...

    @abc.abstractmethod
    async def reconcile_by_id(self, plink_id: str) -> ExecutionResult:
        """Fetch an existing Payment Link by its Razorpay ID.

        Used when razorpay_link_id is stored but status is unknown.
        """
        ...

    @abc.abstractmethod
    async def reconcile_by_reference_id(
        self, reference_id: str
    ) -> list[ExecutionResult]:
        """Fetch Payment Links matching a reference_id.

        Returns all matching results. Caller verifies exact match.
        """
        ...
