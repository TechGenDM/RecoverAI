from google import genai
from google.genai import types

from app.config import settings
from app.schemas import RecoveryContext, RecoveryDecisionSchema

from .base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    def __init__(self):
        self.settings = settings
        if not self.settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set")
        # Sync client initialized once; async calls via client.aio
        self.client = genai.Client(
            api_key=self.settings.GEMINI_API_KEY.get_secret_value()
        )

    @staticmethod
    def build_prompt(context: RecoveryContext) -> str:
        return f"""
You are an AI recovery agent. Analyze the following payment failure context and decide the best recovery action.

Monetary Unit Convention:
Razorpay amounts are stored in the smallest currency subunit.
For INR, 100 paise = ₹1.
amount_paise=50000 means ₹500.00 INR.
Use amount_inr for human-readable reasoning.
Never interpret amount_paise as rupees.

Context:
{context.model_dump_json(indent=2)}

Decide the best action from:
- SEND_PAYMENT_LINK: Customer can likely pay if provided a link.
- WAIT: Wait for a temporary issue to resolve or for a better time to contact.
- ESCALATE: Requires human intervention (e.g. suspected fraud).
- STOP: Unrecoverable or policy violation.

Provide your reasoning, risk factors, and confidence level.
"""

    async def analyze_case(
        self, context: RecoveryContext
    ) -> tuple[RecoveryDecisionSchema, str]:
        prompt = self.build_prompt(context)

        response = await self.client.aio.models.generate_content(
            model=self.settings.LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RecoveryDecisionSchema,
            ),
        )

        parsed = response.parsed
        if not isinstance(parsed, RecoveryDecisionSchema):
            raise TypeError(
                "Failed to parse Gemini response into RecoveryDecisionSchema"
            )

        return parsed, response.text or ""
