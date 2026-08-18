from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class MarketingBroadcastRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    channels: List[Literal["sms", "telegram"]] = Field(..., min_items=1)
    client_ids: Optional[List[int]] = Field(None, description="If omitted, send to all active clients")


class ChannelBroadcastRequest(BaseModel):
    """Single-channel broadcast; the channel comes from the route."""

    message: str = Field(..., min_length=1, max_length=2000)
    client_ids: Optional[List[int]] = Field(None, description="Ignored when send_to_all is true")
    send_to_all: bool = Field(False, description="Send to every active client")


class ChannelResult(BaseModel):
    channel: Literal["sms", "telegram"]
    attempted: int
    sent: int
    failed: int
    errors: List[str] = Field(default_factory=list)


class MarketingBroadcastResponse(BaseModel):
    total_recipients: int
    results: List[ChannelResult]


class MarketingClient(BaseModel):
    id: int
    first_name: str
    last_name: str
    phone: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    reachable_by_sms: bool
    reachable_by_telegram: bool


class MarketingStats(BaseModel):
    total_clients: int
    sms_reachable: int
    telegram_reachable: int
    unreachable: int
    total_broadcasts: int
    total_messages_sent: int
    total_messages_failed: int
    last_broadcast_at: Optional[datetime] = None


class BroadcastHistoryItem(BaseModel):
    id: int
    channel: Literal["sms", "telegram"]
    message: str
    total_recipients: int
    attempted: int
    sent: int
    failed: int
    error_summary: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TelegramConnectionStatus(BaseModel):
    connected: bool
    bot_username: Optional[str] = None
    error: Optional[str] = None


class SmsConnectionStatus(BaseModel):
    connected: bool
    balance: Optional[int] = Field(None, description="Remaining SMS on the Eskiz account")
    sender: Optional[str] = None
    error: Optional[str] = None

