"""Microsoft Graph, app-only: the app sends AS one mailbox (`graph_sender`) with the
Mail.Send APPLICATION permission and a client secret — no user sign-in.

`POST /users/{sender}/sendMail` carries to / cc / bcc / reply-to and the attachments
inline (base64). Graph caps one request at 4 MB, so inline attachments here are kept
under 3 MB in total; larger files need an upload session, which is a deliberate
"not yet" — the message is rejected with that reason rather than half-sent.
"""
from __future__ import annotations

import base64
import time

import httpx

from ..messages import EmailMessage, bare_address
from ..settings import NotifySettings
from .base import SendResult

GRAPH = "https://graph.microsoft.com/v1.0"
INLINE_LIMIT = 3 * 1024 * 1024


class GraphNotConfigured(RuntimeError):
    pass


class GraphTransport:
    name = "graph"

    def __init__(self, settings: NotifySettings, *, http: httpx.Client | None = None) -> None:
        missing = [k for k in ("graph_tenant_id", "graph_client_id", "graph_client_secret",
                               "graph_sender") if not getattr(settings, k).strip()]
        if missing:
            raise GraphNotConfigured("NOTIFY_EMAIL_TRANSPORT=graph but "
                                     + ", ".join(f"NOTIFY_{m.upper()}" for m in missing)
                                     + " is not set")
        self._s = settings
        self._http = http or httpx.Client(timeout=settings.graph_timeout_seconds)
        self._token: str | None = None
        self._expires = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        s = self._s
        response = self._http.post(
            f"https://login.microsoftonline.com/{s.graph_tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": s.graph_client_id,
                  "client_secret": s.graph_client_secret,
                  "scope": "https://graph.microsoft.com/.default"})
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._expires = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def payload(self, message: EmailMessage) -> dict:
        def people(values):
            return [{"emailAddress": {"address": bare_address(v)}} for v in values]

        html = bool(message.body_html)
        mail = {
            "subject": message.subject,
            "body": {"contentType": "HTML" if html else "Text",
                     "content": message.body_html if html else message.body_text},
            "toRecipients": people(message.to),
            "ccRecipients": people(message.cc),
            "bccRecipients": people(message.bcc),
            "replyTo": people(message.reply_to),
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a.filename, "contentType": a.content_type,
                "contentBytes": base64.b64encode(a.content).decode("ascii"),
            } for a in message.attachments],
        }
        if message.headers:
            mail["internetMessageHeaders"] = [
                {"name": k if k.lower().startswith("x-") else f"X-{k}", "value": v}
                for k, v in message.headers.items()]
        return {"message": mail, "saveToSentItems": self._s.graph_save_to_sent_items}

    def send(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> SendResult:
        if sum(a.size for a in message.attachments) > INLINE_LIMIT:
            return SendResult("rejected", error="attachments over 3 MB in total need a Graph "
                                                "upload session, which this transport does not do")
        # The app sends as its configured mailbox; Graph ignores a different From.
        sender = self._s.graph_sender
        try:
            token = self._access_token()
            response = self._http.post(f"{GRAPH}/users/{sender}/sendMail", json=self.payload(message),
                                       headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPStatusError as exc:
            return SendResult("failed", error=f"Graph token request failed: HTTP {exc.response.status_code}")
        except httpx.HTTPError as exc:
            return SendResult("failed", error=f"Graph could not be reached: {type(exc).__name__}: {exc}")
        if response.status_code == 202:
            return SendResult("sent", provider_ref=response.headers.get("request-id"))
        detail = (response.text or "")[:300]
        if response.status_code in (400, 413, 422):
            return SendResult("rejected", error=f"Graph refused it (HTTP {response.status_code}): {detail}")
        return SendResult("failed", error=f"Graph error (HTTP {response.status_code}): {detail}")
