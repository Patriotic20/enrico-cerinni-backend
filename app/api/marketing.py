import json
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.database import get_db
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.marketing import (
    BroadcastHistoryItem,
    ChannelBroadcastRequest,
    ChannelResult,
    MarketingBroadcastRequest,
    MarketingBroadcastResponse,
    MarketingClient,
    MarketingStats,
    TelegramConnectionStatus,
)
from app.services.marketing_service import MarketingService


router = APIRouter(prefix="/marketing", tags=["Marketing"])


def _to_response(total: int, results: dict) -> MarketingBroadcastResponse:
    return MarketingBroadcastResponse(
        total_recipients=total,
        results=[
            ChannelResult(
                channel=ch,
                attempted=data["attempted"],
                sent=data["sent"],
                failed=data["failed"],
                errors=data["errors"],
            )
            for ch, data in results.items()
        ],
    )


MAX_IMAGE_BYTES = 10 * 1024 * 1024  # matches the 10MB limit enforced by the UI


def _parse_client_ids(raw: Optional[str]) -> Optional[List[int]]:
    """The multipart form sends client_ids as a JSON-encoded array."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="client_ids must be a JSON array of integers")
    if not isinstance(parsed, list) or not all(isinstance(i, int) for i in parsed):
        raise HTTPException(status_code=400, detail="client_ids must be a JSON array of integers")
    return parsed or None


async def _run_broadcast(
    service: MarketingService,
    message: str,
    channels: List[str],
    client_ids: Optional[List[int]],
    user_id: Optional[int],
    image: Optional[Tuple[str, bytes, str]] = None,
) -> MarketingBroadcastResponse:
    total, results = await service.broadcast(
        message=message,
        channels=channels,
        client_ids=client_ids,
        image=image,
    )
    service.record_broadcast(
        message=message,
        total_recipients=total,
        results=results,
        created_by=user_id,
    )
    return _to_response(total, results)


@router.post("/broadcast", response_model=ResponseModel)
async def broadcast_message(
    payload: MarketingBroadcastRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send one message across any combination of channels."""
    service = MarketingService(db)
    response = await _run_broadcast(
        service, payload.message, payload.channels, payload.client_ids, current_user.id
    )
    return ResponseModel(success=True, data=response, message="Broadcast completed")


@router.post("/sms/broadcast", response_model=ResponseModel)
async def broadcast_sms(
    payload: ChannelBroadcastRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send an SMS-only broadcast."""
    service = MarketingService(db)
    client_ids = None if payload.send_to_all else (payload.client_ids or None)
    response = await _run_broadcast(
        service, payload.message, ["sms"], client_ids, current_user.id
    )
    return ResponseModel(success=True, data=response, message="SMS broadcast completed")


@router.post("/telegram/broadcast", response_model=ResponseModel)
async def broadcast_telegram(
    message: str = Form(..., min_length=1, max_length=2000),
    client_ids: Optional[str] = Form(None, description="JSON array of client IDs"),
    send_to_all: bool = Form(False),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send a Telegram-only broadcast, optionally with an image.

    Accepts multipart/form-data because the UI can attach a photo; when one is
    present the message is delivered as the photo caption via sendPhoto.
    """
    service = MarketingService(db)
    target_ids = None if send_to_all else _parse_client_ids(client_ids)

    image_payload = None
    if image is not None and image.filename:
        content = await image.read()
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds the 10MB limit")
        image_payload = (
            image.filename,
            content,
            image.content_type or "application/octet-stream",
        )

    response = await _run_broadcast(
        service, message, ["telegram"], target_ids, current_user.id, image_payload
    )
    return ResponseModel(
        success=True, data=response, message="Telegram broadcast completed"
    )


@router.get("/stats", response_model=ResponseModel)
async def get_marketing_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Audience reach and aggregate delivery counters."""
    stats = MarketingService(db).get_stats()
    return ResponseModel(
        success=True,
        data=MarketingStats(**stats),
        message="Marketing statistics retrieved successfully",
    )


@router.get("/history", response_model=ResponseModel)
async def get_broadcast_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    channel: Optional[str] = Query(None, description="Filter by channel: sms or telegram"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Past broadcasts, newest first."""
    rows = MarketingService(db).get_history(limit=limit, offset=offset, channel=channel)
    return ResponseModel(
        success=True,
        data=[BroadcastHistoryItem.model_validate(row) for row in rows],
        message="Broadcast history retrieved successfully",
    )


@router.get("/clients", response_model=ResponseModel)
async def get_marketing_clients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Active clients annotated with the channels they can be reached through."""
    clients = MarketingService(db).get_clients()
    return ResponseModel(
        success=True,
        data=[
            MarketingClient(
                id=c.id,
                first_name=c.first_name,
                last_name=c.last_name,
                phone=c.phone,
                telegram_chat_id=c.telegram_chat_id,
                reachable_by_sms=bool(c.phone),
                reachable_by_telegram=bool(c.telegram_chat_id),
            )
            for c in clients
        ],
        message="Marketing clients retrieved successfully",
    )


@router.get("/telegram/test", response_model=ResponseModel)
async def test_telegram_connection(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Verify the configured Telegram bot token via getMe."""
    status = await MarketingService(db).test_telegram_connection()
    return ResponseModel(
        success=status["connected"],
        data=TelegramConnectionStatus(**status),
        message=(
            "Telegram bot connection is healthy"
            if status["connected"]
            else status.get("error") or "Telegram bot connection failed"
        ),
    )
