import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.webhook_service import (
    WebhookIngestionService,
    verify_razorpay_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post(
    "/razorpay",
    status_code=status.HTTP_200_OK,
    summary="Inbound Razorpay Webhook Endpoint",
    description="Receives, cryptographically verifies, and transactionally ingests Razorpay webhooks.",
)
async def razorpay_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_razorpay_signature: Annotated[str | None, Header()] = None,
    x_razorpay_event_id: Annotated[str | None, Header()] = None,
):
    # 1. Require signature header
    if not x_razorpay_signature:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "missing_signature",
                "message": "X-Razorpay-Signature header required",
            },
        )

    # 2. Require event ID header
    if not x_razorpay_event_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "missing_event_id",
                "message": "X-Razorpay-Event-Id header required",
            },
        )

    # 3. Read exact raw request body for HMAC verification
    raw_body = await request.body()
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET.get_secret_value()

    # 4. Cryptographic signature check (constant-time, never logging secrets)
    if not verify_razorpay_signature(raw_body, x_razorpay_signature, webhook_secret):
        logger.warning(
            "Webhook rejected: Invalid signature for event %s",
            x_razorpay_event_id,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "invalid_signature",
                "message": "Signature verification failed",
            },
        )

    # 5. Parse JSON payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("Payload must be a JSON object")
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "malformed_json",
                "message": "Request body must be valid JSON",
            },
        )

    # 6. Ingest webhook within a transactional boundary
    try:
        status_code, response_data = await WebhookIngestionService.process_webhook(
            event_id=x_razorpay_event_id,
            payload=payload,
            db=db,
        )
        return JSONResponse(status_code=status_code, content=response_data)
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error during webhook ingestion for event %s",
            x_razorpay_event_id,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_server_error",
                "message": "An unexpected error occurred",
            },
        )
