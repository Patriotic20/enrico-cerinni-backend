from typing import List, Optional, Tuple
import asyncio
import httpx

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.broadcast import BroadcastHistory
from app.models.client import Client
from app.config import settings
from app.services.eskiz_sms import eskiz_client
from app.services import integration_settings


BROADCAST_CONCURRENCY = 20


class MarketingService:
    def __init__(self, db: Session):
        self.db = db
        integration_settings.apply(db)  # credentials saved from the UI

    def _get_recipients(self, client_ids: List[int] | None) -> List[Client]:
        query = self.db.query(Client).filter(Client.is_active == True)
        if client_ids:
            query = query.filter(Client.id.in_(client_ids))
        return query.all()

    @staticmethod
    async def _telegram_post(client: httpx.AsyncClient, method: str, **kwargs) -> bool:
        """POST to the Bot API, waiting out 429 rate limits (retry_after) up to 3 times."""
        url = f"https://api.telegram.org/bot{settings.notification.telegram_bot_token}/{method}"
        for _ in range(4):
            try:
                resp = await client.post(url, **kwargs)
            except Exception:
                return False
            if resp.status_code == 429:
                try:
                    wait = resp.json().get("parameters", {}).get("retry_after", 1)
                except Exception:
                    wait = 1
                await asyncio.sleep(min(float(wait), 30))
                continue
            try:
                return resp.status_code == 200 and resp.json().get("ok", False)
            except Exception:
                return False
        return False

    async def _send_telegram_message(
        self, client: httpx.AsyncClient, chat_id: str, text: str
    ) -> bool:
        if not settings.notification.telegram_bot_token:
            return False
        return await self._telegram_post(
            client, "sendMessage", json={"chat_id": chat_id, "text": text}
        )

    async def _send_telegram_photo(
        self, client: httpx.AsyncClient, chat_id: str, caption: str, image: Tuple[str, bytes, str]
    ) -> bool:
        """Send a photo with the message as its caption. `image` is (filename, content, content_type)."""
        if not settings.notification.telegram_bot_token:
            return False
        filename, content, content_type = image
        return await self._telegram_post(
            client,
            "sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"photo": (filename, content, content_type)},
        )

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

    @staticmethod
    def sms_configured() -> bool:
        n = settings.notification
        return eskiz_client.is_configured() or bool(
            n.sms_provider != "eskiz" and n.sms_base_url and n.sms_api_key
        )

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
        has_phone = and_(Client.phone.isnot(None), Client.phone != "")
        has_tg = and_(Client.telegram_chat_id.isnot(None), Client.telegram_chat_id != "")
        total_clients, sms_reachable, telegram_reachable, unreachable = self.db.query(
            func.count(Client.id),
            func.count(Client.id).filter(has_phone),
            func.count(Client.id).filter(has_tg),
            func.count(Client.id).filter(~has_phone, ~has_tg),
        ).filter(Client.is_active == True).one()

        totals = self.db.query(
            func.count(BroadcastHistory.id),
            func.coalesce(func.sum(BroadcastHistory.sent), 0),
            func.coalesce(func.sum(BroadcastHistory.failed), 0),
            func.max(BroadcastHistory.created_at),
        ).one()

        return {
            "total_clients": total_clients,
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
            return {"connected": False, "configured": False, "error": "Telegram bot token is not configured"}

        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url)
            except Exception as exc:
                return {"connected": False, "configured": True, "error": str(exc)}

        if resp.status_code != 200:
            return {"connected": False, "configured": True, "error": f"Telegram API returned HTTP {resp.status_code}"}

        body = resp.json()
        if not body.get("ok"):
            return {"connected": False, "configured": True, "error": body.get("description", "Telegram API rejected the token")}

        return {"connected": True, "configured": True, "bot_username": body.get("result", {}).get("username")}

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
        recipients = [
            (c.id, c.telegram_chat_id, c.phone) for c in self._get_recipients(client_ids)
        ]
        total = len(recipients)
        # End the read transaction so the pooled connection isn't held idle for
        # the minutes a large broadcast can take.
        self.db.commit()

        results = {ch: {"attempted": 0, "sent": 0, "failed": 0, "errors": []} for ch in channels}

        # Bounded fan-out: thousands of simultaneous sends only earn 429s from
        # Telegram and exhaust sockets.
        gate = asyncio.Semaphore(BROADCAST_CONCURRENCY)

        async def gated(coro):
            async with gate:
                return await coro

        coros = []
        task_metadata: List[tuple] = []  # (channel, client id)

        async with httpx.AsyncClient(timeout=30) as tg:
            for client_id, chat_id, phone in recipients:
                if "telegram" in channels and chat_id:
                    results["telegram"]["attempted"] += 1
                    if image is not None:
                        coro = self._send_telegram_photo(tg, chat_id, message, image)
                    else:
                        coro = self._send_telegram_message(tg, chat_id, message)
                    coros.append(gated(coro))
                    task_metadata.append(("telegram", client_id))
                if "sms" in channels and phone:
                    results["sms"]["attempted"] += 1
                    coros.append(gated(self._send_sms_message(phone, message)))
                    task_metadata.append(("sms", client_id))

            outcomes = await asyncio.gather(*coros, return_exceptions=True) if coros else []
        if coros:
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

