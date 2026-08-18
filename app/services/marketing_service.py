from typing import List, Optional, Tuple
import asyncio
import httpx

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.broadcast import BroadcastHistory
from app.models.client import Client
from app.config import settings
from app.services.eskiz_sms import eskiz_client


class MarketingService:
    def __init__(self, db: Session):
        self.db = db

    def _get_recipients(self, client_ids: List[int] | None) -> List[Client]:
        query = self.db.query(Client).filter(Client.is_active == True)
        if client_ids:
            query = query.filter(Client.id.in_(client_ids))
        return query.all()

    async def _send_telegram_message(self, chat_id: str, text: str) -> bool:
        if not settings.notification.telegram_bot_token:
            return False
        url = f"https://api.telegram.org/bot{settings.notification.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(url, json={"chat_id": chat_id, "text": text})
                return resp.status_code == 200 and resp.json().get("ok", False)
            except Exception:
                return False

    async def _send_telegram_photo(
        self, chat_id: str, caption: str, image: Tuple[str, bytes, str]
    ) -> bool:
        """Send a photo with the message as its caption. `image` is (filename, content, content_type)."""
        if not settings.notification.telegram_bot_token:
            return False
        url = f"https://api.telegram.org/bot{settings.notification.telegram_bot_token}/sendPhoto"
        filename, content, content_type = image
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    url,
                    data={"chat_id": chat_id, "caption": caption[:1024]},
                    files={"photo": (filename, content, content_type)},
                )
                return resp.status_code == 200 and resp.json().get("ok", False)
            except Exception:
                return False

    async def _send_sms_message(self, phone: str, text: str) -> bool:
        if settings.notification.sms_provider == "eskiz" or eskiz_client.is_configured():
            ok, error = await eskiz_client.send_sms(phone, text)
            if not ok and error:
                # Raising lets broadcast() put the reason into the error summary.
                raise RuntimeError(error)
            return ok
        # Fallback: generic HTTP provider configured via sms_base_url/sms_api_key
        if not settings.notification.sms_base_url or not settings.notification.sms_api_key:
            return False
        headers = {"Authorization": f"Bearer {settings.notification.sms_api_key}"}
        payload = {"to": phone, "from": settings.notification.sms_from_number, "message": text}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.post(settings.notification.sms_base_url.rstrip("/") + "/send", json=payload, headers=headers)
                return 200 <= resp.status_code < 300
            except Exception:
                return False

    async def test_sms_connection(self) -> dict:
        """Verify the configured Eskiz credentials and fetch the remaining limit."""
        return await eskiz_client.test_connection()

    def get_clients(self, search: Optional[str] = None) -> List[Client]:
        """Active clients with the channels each one can be reached through."""
        query = self.db.query(Client).filter(Client.is_active == True)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                Client.first_name.ilike(pattern)
                | Client.last_name.ilike(pattern)
                | Client.phone.ilike(pattern)
            )
        return query.order_by(Client.first_name, Client.last_name).all()

    def get_stats(self) -> dict:
        """Audience reach plus aggregate delivery counters from past broadcasts."""
        clients = self.db.query(Client).filter(Client.is_active == True).all()

        sms_reachable = sum(1 for c in clients if c.phone)
        telegram_reachable = sum(1 for c in clients if c.telegram_chat_id)
        unreachable = sum(1 for c in clients if not c.phone and not c.telegram_chat_id)

        totals = self.db.query(
            func.count(BroadcastHistory.id),
            func.coalesce(func.sum(BroadcastHistory.sent), 0),
            func.coalesce(func.sum(BroadcastHistory.failed), 0),
            func.max(BroadcastHistory.created_at),
        ).one()

        return {
            "total_clients": len(clients),
            "sms_reachable": sms_reachable,
            "telegram_reachable": telegram_reachable,
            "unreachable": unreachable,
            "total_broadcasts": int(totals[0] or 0),
            "total_messages_sent": int(totals[1] or 0),
            "total_messages_failed": int(totals[2] or 0),
            "last_broadcast_at": totals[3],
        }

    def get_history(self, limit: int = 50, offset: int = 0, channel: Optional[str] = None) -> List[BroadcastHistory]:
        query = self.db.query(BroadcastHistory)
        if channel:
            query = query.filter(BroadcastHistory.channel == channel)
        return (
            query.order_by(BroadcastHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    async def test_telegram_connection(self) -> dict:
        """Call getMe on the Telegram Bot API to verify the configured token."""
        token = settings.notification.telegram_bot_token
        if not token:
            return {"connected": False, "error": "Telegram bot token is not configured"}

        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url)
            except Exception as exc:
                return {"connected": False, "error": str(exc)}

        if resp.status_code != 200:
            return {"connected": False, "error": f"Telegram API returned HTTP {resp.status_code}"}

        body = resp.json()
        if not body.get("ok"):
            return {"connected": False, "error": body.get("description", "Telegram API rejected the token")}

        return {"connected": True, "bot_username": body.get("result", {}).get("username")}

    def record_broadcast(
        self,
        message: str,
        total_recipients: int,
        results: dict,
        created_by: Optional[int] = None,
    ) -> None:
        """Persist one history row per channel so the UI can show past sends."""
        for channel, data in results.items():
            errors = data.get("errors") or []
            self.db.add(
                BroadcastHistory(
                    channel=channel,
                    message=message,
                    total_recipients=total_recipients,
                    attempted=data.get("attempted", 0),
                    sent=data.get("sent", 0),
                    failed=data.get("failed", 0),
                    error_summary="; ".join(errors[:10]) if errors else None,
                    created_by=created_by,
                )
            )
        self.db.commit()

    async def broadcast(
        self,
        message: str,
        channels: List[str],
        client_ids: List[int] | None,
        image: Optional[Tuple[str, bytes, str]] = None,
    ) -> Tuple[int, dict]:
        recipients = self._get_recipients(client_ids)
        total = len(recipients)

        results = {ch: {"attempted": 0, "sent": 0, "failed": 0, "errors": []} for ch in channels}

        tasks: List[asyncio.Task] = []
        task_metadata: List[tuple] = []  # (channel, index)

        for client in recipients:
            if "telegram" in channels and client.telegram_chat_id:
                results["telegram"]["attempted"] += 1
                if image is not None:
                    coro = self._send_telegram_photo(client.telegram_chat_id, message, image)
                else:
                    coro = self._send_telegram_message(client.telegram_chat_id, message)
                tasks.append(asyncio.create_task(coro))
                task_metadata.append(("telegram", client.id))
            if "sms" in channels and client.phone:
                results["sms"]["attempted"] += 1
                tasks.append(asyncio.create_task(self._send_sms_message(client.phone, message)))
                task_metadata.append(("sms", client.id))

        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for (channel, client_id), ok in zip(task_metadata, outcomes):
                if isinstance(ok, Exception):
                    results[channel]["failed"] += 1
                    results[channel]["errors"].append(f"client {client_id}: {str(ok)}")
                else:
                    if ok:
                        results[channel]["sent"] += 1
                    else:
                        results[channel]["failed"] += 1
        return total, results

