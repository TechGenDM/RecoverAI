"""Executor factory — returns the correct executor for the given mode."""

from app.services.executor.base import ExecutionResult, RecoveryExecutor
from app.services.executor.simulated_executor import SimulatedRecoveryExecutor

__all__ = [
    "ExecutionResult",
    "RecoveryExecutor",
    "SimulatedRecoveryExecutor",
    "get_executor",
]


def get_executor(mode: str) -> RecoveryExecutor:
    """Factory: return the correct executor for the given mode.

    LIVE mode requires a RazorpayClient — import is deferred to avoid
    structural dependency in SIMULATED mode.
    """
    if mode == "LIVE":
        # Deferred import: LiveRecoveryExecutor and RazorpayClient are
        # only imported when LIVE mode is actually used
        from app.services.executor.live_executor import LiveRecoveryExecutor
        from app.services.razorpay_client import RazorpayClient

        client = RazorpayClient()
        return LiveRecoveryExecutor(client=client)

    # SIMULATED or any test mode
    return SimulatedRecoveryExecutor()
