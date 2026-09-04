"""Async HTTP client for Razorpay Payment Links API.

Encapsulates all external Razorpay communication behind typed methods.
Never exposes credentials beyond the HTTP Basic Auth header.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class RazorpayAPIError(Exception):
    """Definite failure from Razorpay (4xx response)."""

    def __init__(self, status_code: int, error_code: str, description: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.description = description
        super().__init__(f"Razorpay {status_code}: {error_code} — {description}")


class RazorpayTimeoutError(Exception):
    """Network timeout or connection error — outcome UNKNOWN."""


class RazorpayDuplicateReferenceError(RazorpayAPIError):
    """POST returned a duplicate reference_id error (defensive fallback)."""


@dataclass(frozen=True)
class PaymentLinkResult:
    """Parsed result from a successful Payment Link creation or fetch."""

    plink_id: str
    short_url: str
    reference_id: str
    expire_by: int
    status: str
    amount: int
    raw: dict[str, Any]


class RazorpayClient:
    """Async client for Razorpay Payment Links API (v1).

    Uses httpx with Basic Auth. Never stores credentials in memory
    beyond what httpx needs for the request lifecycle.
    """

    def __init__(self) -> None:
        self._base_url = settings.RAZORPAY_BASE_URL.rstrip("/")
        self._timeout = settings.EXECUTOR_TIMEOUT_SECONDS

    def _auth(self) -> tuple[str, str]:
        """Returns Basic Auth tuple. Secret is read at call time, not cached."""
        return (
            settings.RAZORPAY_KEY_ID,
            settings.RAZORPAY_KEY_SECRET.get_secret_value(),
        )

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str,
        reference_id: str,
        expire_by: int,
        description: str,
        customer: dict[str, str] | None = None,
    ) -> PaymentLinkResult:
        """POST /v1/payment_links — create a new Payment Link.

        Raises:
            RazorpayDuplicateReferenceError: if reference_id already exists
            RazorpayAPIError: for other 4xx errors
            RazorpayTimeoutError: for timeout/network errors
        """
        payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "accept_partial": False,
            "expire_by": expire_by,
            "reference_id": reference_id,
            "description": description,
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
        }
        if customer:
            payload["customer"] = customer

        try:
            async with httpx.AsyncClient(
                auth=self._auth(),
                timeout=self._timeout,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/v1/payment_links",
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise RazorpayTimeoutError(str(exc)) from exc

        if response.status_code in (200, 201):
            data = response.json()
            return PaymentLinkResult(
                plink_id=data["id"],
                short_url=data.get("short_url", ""),
                reference_id=data.get("reference_id", reference_id),
                expire_by=data.get("expire_by", expire_by),
                status=data.get("status", "created"),
                amount=data.get("amount", amount),
                raw=data,
            )

        # Parse error response
        error_data = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        error_obj = error_data.get("error", {})
        error_code = error_obj.get("code", "UNKNOWN")
        error_desc = error_obj.get("description", response.text[:200])

        # Detect duplicate reference_id (defensive fallback)
        if response.status_code == 400 and "reference_id" in error_desc.lower():
            raise RazorpayDuplicateReferenceError(
                status_code=response.status_code,
                error_code=error_code,
                description=error_desc,
            )

        raise RazorpayAPIError(
            status_code=response.status_code,
            error_code=error_code,
            description=error_desc,
        )

    async def fetch_payment_link(self, plink_id: str) -> PaymentLinkResult:
        """GET /v1/payment_links/:id — fetch a single Payment Link by ID.

        Raises:
            RazorpayAPIError: for 4xx errors (including 404)
            RazorpayTimeoutError: for timeout/network errors
        """
        try:
            async with httpx.AsyncClient(
                auth=self._auth(),
                timeout=self._timeout,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/v1/payment_links/{plink_id}",
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise RazorpayTimeoutError(str(exc)) from exc

        if response.status_code == 200:
            data = response.json()
            return PaymentLinkResult(
                plink_id=data["id"],
                short_url=data.get("short_url", ""),
                reference_id=data.get("reference_id", ""),
                expire_by=data.get("expire_by", 0),
                status=data.get("status", ""),
                amount=data.get("amount", 0),
                raw=data,
            )

        error_data = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        error_obj = error_data.get("error", {})
        raise RazorpayAPIError(
            status_code=response.status_code,
            error_code=error_obj.get("code", "UNKNOWN"),
            description=error_obj.get("description", response.text[:200]),
        )

    async def fetch_payment_links_by_reference_id(
        self, reference_id: str
    ) -> list[PaymentLinkResult]:
        """GET /v1/payment_links/?reference_id=<ref> — fetch by reference_id.

        Returns a list of matching links. Caller must verify exact match.

        Raises:
            RazorpayTimeoutError: for timeout/network errors
            RazorpayAPIError: for non-200 responses
        """
        try:
            async with httpx.AsyncClient(
                auth=self._auth(),
                timeout=self._timeout,
            ) as client:
                response = await client.get(
                    f"{self._base_url}/v1/payment_links/",
                    params={"reference_id": reference_id},
                )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise RazorpayTimeoutError(str(exc)) from exc

        if response.status_code == 200:
            data = response.json()
            items = data.get("payment_links", data.get("items", []))
            results: list[PaymentLinkResult] = []
            for item in items:
                results.append(
                    PaymentLinkResult(
                        plink_id=item["id"],
                        short_url=item.get("short_url", ""),
                        reference_id=item.get("reference_id", ""),
                        expire_by=item.get("expire_by", 0),
                        status=item.get("status", ""),
                        amount=item.get("amount", 0),
                        raw=item,
                    )
                )
            return results

        error_data = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        error_obj = error_data.get("error", {})
        raise RazorpayAPIError(
            status_code=response.status_code,
            error_code=error_obj.get("code", "UNKNOWN"),
            description=error_obj.get("description", response.text[:200]),
        )
