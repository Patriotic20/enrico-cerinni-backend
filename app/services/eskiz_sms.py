"""Eskiz.uz SMS provider client (https://notify.eskiz.uz).

Authentication is email/password from the Eskiz cabinet; the API issues a
bearer token valid for ~30 days. The token is cached in-process and re-issued
automatically when the API answers 401.

Test accounts (before a contract with Eskiz is approved) can only send the
exact texts Eskiz whitelists — e.g. "This is test from Eskiz" — using the
shared sender nickname "4546". Any other text is rejected by the API.
"""

import asyncio
import re
from typing import Optional, Tuple

import httpx

from app.config import settings

BASE_URL = "https://notify.eskiz.uz/api"
DEFAULT_SENDER = "4546"  # shared nickname, the only one test accounts may use


class EskizAuthError(Exception):
    pass


class EskizSmsClient:
    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._lock = asyncio.Lock()

    @staticmethod
    def is_configured() -> bool:
        n = settings.notification
        return bool(n.eskiz_email and n.eskiz_password)

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Eskiz expects digits only in the 998XXXXXXXXX format."""
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) == 9:
            digits = "998" + digits
        return digits

    async def _login(self, client: httpx.AsyncClient) -> str:
        n = settings.notification
        resp = await client.post(
            f"{BASE_URL}/auth/login",
            data={"email": n.eskiz_email, "password": n.eskiz_password},
        )
        if resp.status_code != 200:
            raise EskizAuthError(f"Eskiz auth failed: HTTP {resp.status_code}")
        token = (resp.json().get("data") or {}).get("token")
        if not token:
            raise EskizAuthError("Eskiz auth failed: no token in response")
        return token

    async def _get_token(self, client: httpx.AsyncClient, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token:
                self._token = await self._login(client)
            return self._token

    @staticmethod
    async def _post_send(
        client: httpx.AsyncClient, token: str, mobile: str, text: str, sender: str
    ) -> httpx.Response:
        return await client.post(
            f"{BASE_URL}/message/sms/send",
            data={"mobile_phone": mobile, "message": text, "from": sender},
            headers={"Authorization": f"Bearer {token}"},
        )

    async def send_sms(self, phone: str, text: str) -> Tuple[bool, Optional[str]]:
        """Send one SMS. Returns (ok, error); re-authenticates once on 401."""
        if not self.is_configured():
            return False, "Eskiz credentials are not configured"

        mobile = self.normalize_phone(phone)
        if len(mobile) != 12 or not mobile.startswith("998"):
            return False, f"invalid phone number: {phone}"

        sender = settings.notification.sms_from_number or DEFAULT_SENDER
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                token = await self._get_token(client)
                resp = await self._post_send(client, token, mobile, text, sender)
                if resp.status_code == 401:
                    token = await self._get_token(client, force=True)
                    resp = await self._post_send(client, token, mobile, text, sender)
            except EskizAuthError as exc:
                return False, str(exc)
            except Exception as exc:
                return False, f"Eskiz request failed: {exc}"

        if 200 <= resp.status_code < 300:
            return True, None
        try:
            detail = resp.json().get("message") or resp.text[:200]
        except Exception:
            detail = resp.text[:200]
        return False, f"Eskiz rejected the message (HTTP {resp.status_code}): {detail}"

    async def test_connection(self) -> dict:
        """Verify credentials by logging in, and fetch the remaining SMS limit."""
        if not self.is_configured():
            return {
                "connected": False,
                "error": "Eskiz credentials are not configured "
                "(NOTIFICATION__ESKIZ_EMAIL / NOTIFICATION__ESKIZ_PASSWORD)",
            }

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                token = await self._get_token(client, force=True)
            except EskizAuthError as exc:
                return {"connected": False, "error": str(exc)}
            except Exception as exc:
                return {"connected": False, "error": f"Eskiz request failed: {exc}"}

            balance = None
            try:
                resp = await client.get(
                    f"{BASE_URL}/user/get-limit",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    balance = (resp.json().get("data") or {}).get("balance")
            except Exception:
                pass  # the balance is informational; auth already succeeded

        return {
            "connected": True,
            "balance": balance,
            "sender": settings.notification.sms_from_number or DEFAULT_SENDER,
        }


eskiz_client = EskizSmsClient()
