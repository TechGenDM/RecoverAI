from abc import ABC, abstractmethod

from app.schemas import RecoveryContext, RecoveryDecisionSchema


class BaseLLMProvider(ABC):
    @abstractmethod
    async def analyze_case(self, context: RecoveryContext) -> tuple[RecoveryDecisionSchema, str]:
        """
        Analyzes the recovery context and returns a RecoveryDecisionSchema and the raw response string.
        """
