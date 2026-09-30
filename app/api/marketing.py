import json
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_current_admin_user, require_staff
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
    IntegrationSettingsUpdate,
    SmsConnectionStatus,
    TelegramConnectionStatus,
)
from app.config import settings
from app.services.eskiz_sms import eskiz_client
from app.models.client import Client
from app.services import integration_settings, telegram_link
from app.services.marketing_service import MarketingService


router = APIRouter(prefix="/marketing", tags=["Marketing"], dependencies=[Depends(require_staff)])


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


def _require_configured(channels: List[str]) -> None:
    """Refuse up front instead of recording a broadcast where every send failed."""
    if "sms" in channels and not MarketingService.sms_configured():
        raise HTTPException(status_code=400, detail="SMS provayder (Eskiz) sozlanmagan")
    if "telegram" in channels and not settings.notification.telegram_bot_token:
        raise HTTPException(status_code=400, detail="Telegram bot sozlanmagan")


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
    _require_configured(channels)
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


def _image_mime(data: bytes):
    """Type from magic bytes; the client-sent content type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    try:
        await telegram_link.sync(db)  # pick up clients who just pressed Start
    except Exception:
        pass

    image_payload = None
    if image is not None and image.filename:
        # Read at most one byte past the limit instead of the whole upload.
        content = await image.read(MAX_IMAGE_BYTES + 1)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds the 10MB limit")
        mime = _image_mime(content)
        if not mime:
            raise HTTPException(status_code=400, detail="Only JPEG, PNG or WEBP images are allowed")
        image_payload = (image.filename, content, mime)

    response = await _run_broadcast(
        service, message, ["telegram"], target_ids, current_user.id, image_payload
    )
    return ResponseModel(
        success=True, data=response, message="Telegram broadcast completed"
    )


@router.get("/stats", response_model=ResponseModel)
def get_marketing_stats(
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
def get_broadcast_history(
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
def get_marketing_clients(
    search: Optional[str] = Query(None, max_length=100, description="Filter by name or phone"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Active clients annotated with the channels they can be reached through."""
    clients = MarketingService(db).get_clients(search=search)
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


@router.get("/sms/test", response_model=ResponseModel)
async def test_sms_connection(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Verify the configured Eskiz SMS credentials and show the remaining limit."""
    status = await MarketingService(db).test_sms_connection()
    return ResponseModel(
        success=status["connected"],
        data=SmsConnectionStatus(**status),
        message=(
            "SMS provider connection is healthy"
            if status["connected"]
            else status.get("error") or "SMS provider connection failed"
        ),
    )


@router.get("/sms/templates", response_model=ResponseModel)
async def get_sms_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Templates from the Eskiz cabinet; only approved texts are delivered."""
    integration_settings.apply(db)
    if not eskiz_client.is_configured():
        return ResponseModel(success=True, data=[], message="SMS provayder (Eskiz) sozlanmagan")
    try:
        templates = await eskiz_client.get_templates()
    except Exception as exc:
        return ResponseModel(success=False, data=[], message=str(exc))
    return ResponseModel(success=True, data=templates, message="SMS templates retrieved")


@router.post("/telegram/sync", response_model=ResponseModel)
async def sync_telegram_links(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Link clients who pressed Start via their invite link."""
    integration_settings.apply(db)
    try:
        linked = await telegram_link.sync(db)
    except Exception as exc:
        return ResponseModel(success=False, data=[], message=str(exc))
    return ResponseModel(success=True, data=linked, message=f"{len(linked)} ta mijoz ulandi")


@router.get("/telegram/link/{client_id}", response_model=ResponseModel)
async def get_telegram_link(
    client_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Personal t.me deep link the client opens to connect to the bot."""
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    integration_settings.apply(db)
    try:
        url = await telegram_link.link_url(client_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ResponseModel(success=True, data={"url": url}, message="Link generated")


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


@router.get("/settings", response_model=ResponseModel)
def get_integration_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Which integrations are configured. Secrets are never returned."""
    return ResponseModel(
        success=True,
        data=integration_settings.public_view(db),
        message="Integration settings retrieved",
    )


@router.put("/settings", response_model=ResponseModel)
def update_integration_settings(
    payload: IntegrationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Save Eskiz / Telegram credentials (encrypted). Omitted fields are kept, "" clears."""
    try:
        integration_settings.update(db, payload.model_dump(), current_user.id)
    except integration_settings.SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ResponseModel(
        success=True,
        data=integration_settings.public_view(db),
        message="Sozlamalar saqlandi",
    )
