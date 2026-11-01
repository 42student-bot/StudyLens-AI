"""Canal Messenger : signature des webhooks, lecture des événements, envoi,
téléchargement sécurisé des photos."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .config import Settings
from .http import HttpClient, HttpError

log = logging.getLogger("studylens.messenger")

MESSENGER_TEXT_LIMIT = 2000


class DownloadError(Exception):
    pass


@dataclass
class IncomingMessage:
    psid: str
    mid: str
    text: str = ""
    image_urls: list[str] = field(default_factory=list)


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Valide X-Hub-Signature-256. Sans secret configuré : tout est refusé."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256=") :])


def parse_events(payload: dict) -> list[IncomingMessage]:
    """Extrait les messages utilisateur (texte / photos). Ignore échos,
    accusés de lecture/livraison, postbacks et autocollants."""
    out: list[IncomingMessage] = []
    if payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []) or []:
        for ev in entry.get("messaging", []) or []:
            msg = ev.get("message")
            psid = (ev.get("sender") or {}).get("id")
            if not msg or not psid or msg.get("is_echo"):
                continue
            urls: list[str] = []
            for att in msg.get("attachments", []) or []:
                pl = att.get("payload") or {}
                if att.get("type") == "image" and pl.get("url") and not pl.get("sticker_id"):
                    urls.append(pl["url"])
            text = msg.get("text") or ""
            if urls or text.strip():
                out.append(IncomingMessage(str(psid), str(msg.get("mid", "")), text.strip(), urls))
    return out


def host_allowed(url: str, suffixes: tuple[str, ...]) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.password:
        return False
    return any(host == s.lstrip(".") or host.endswith(s) for s in suffixes)


class MessengerClient:
    def __init__(self, settings: Settings, http: HttpClient):
        self.settings = settings
        self.http = http

    async def send_text(self, psid: str, text: str) -> bool:
        """Envoie un message ; ne lève jamais (un échec d'envoi ne doit pas
        interrompre une analyse). Le jeton est passé en en-tête, jamais en URL."""
        text = text[:MESSENGER_TEXT_LIMIT]
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }
        headers = {"Authorization": f"Bearer {self.settings.fb_page_token}"}
        url = self.settings.graph_url("me/messages")
        for attempt in range(3):
            try:
                resp = await asyncio.to_thread(
                    self.http.request, "POST", url, headers=headers, json=payload, timeout=20.0
                )
            except HttpError:
                resp = None
            if resp is not None and resp.ok:
                return True
            retryable = resp is None or resp.status in (429, 500, 502, 503)
            if not retryable or attempt == 2:
                log.error("envoi Messenger échoué (HTTP %s)", getattr(resp, "status", "réseau"))
                return False
            await asyncio.sleep(1.0 * (attempt + 1))
        return False

    async def send_image(self, psid: str, image_bytes: bytes) -> bool:
        """Téléverse un PNG sur Messenger puis l'envoie comme pièce jointe."""
        headers = {"Authorization": f"Bearer {self.settings.fb_page_token}"}
        upload_url = self.settings.graph_url("me/message_attachments")
        try:
            uploaded = await asyncio.to_thread(
                self.http.request,
                "POST",
                upload_url,
                headers=headers,
                data={"message": __import__("json").dumps({
                    "attachment": {"type": "image", "payload": {"is_reusable": True}}
                })},
                files={"file": ("studylens.png", image_bytes, "image/png")},
                timeout=30.0,
            )
        except Exception as exc:
            log.warning("téléversement image Messenger impossible (%s)", type(exc).__name__)
            return False
        if not uploaded.ok:
            log.warning("téléversement image Messenger échoué (HTTP %s)", uploaded.status)
            return False
        try:
            attachment_id = uploaded.json().get("attachment_id")
        except Exception:
            attachment_id = None
        if not attachment_id:
            log.warning("téléversement image Messenger : attachment_id absent")
            return False
        payload = {
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"attachment": {"type": "image", "payload": {"attachment_id": attachment_id}}},
        }
        try:
            resp = await asyncio.to_thread(
                self.http.request, "POST", self.settings.graph_url("me/messages"),
                headers=headers, json=payload, timeout=20.0
            )
        except HttpError:
            log.warning("envoi image Messenger échoué (erreur réseau)")
            return False
        if not resp.ok:
            log.warning("envoi image Messenger échoué (HTTP %s)", resp.status)
            return False
        return True

    async def download_image(self, url: str) -> bytes:
        if not host_allowed(url, self.settings.image_host_suffixes):
            raise DownloadError("hôte d'image non autorisé")
        try:
            resp = await asyncio.to_thread(
                self.http.request, "GET", url, timeout=30.0, max_bytes=self.settings.max_image_bytes
            )
        except HttpError as exc:
            raise DownloadError(str(exc)) from None
        if not resp.ok:
            raise DownloadError(f"HTTP {resp.status}")
        ctype = resp.headers.get("content-type", "")
        if ctype and not ctype.lower().startswith("image/"):
            raise DownloadError("type de contenu inattendu")
        return resp.body
