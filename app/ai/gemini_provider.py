"""Fournisseur Google Gemini (API generateContent).

Gratuit ou payant selon votre compte : dÃ©clarez GEMINI_FREE_TIER=true seulement
aprÃ¨s avoir vÃ©rifiÃ© le quota gratuit et ses conditions (notamment l'usage des
donnÃ©es) dans la documentation Google actuelle.
"""
from __future__ import annotations

import asyncio
import base64

from ..config import ProviderConfig
from ..http import HttpClient, HttpError
from .base import Image, LLMResult, Provider, ProviderError

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, cfg: ProviderConfig, http: HttpClient, timeout: float = 120.0):
        self.cfg = cfg
        self.http = http
        self.timeout = timeout
        self.free_tier = cfg.free_tier
        self.price_in = cfg.price_in
        self.price_out = cfg.price_out

    def build_payload(self, system, user_text, images: list[Image], max_tokens) -> dict:
        parts: list[dict] = []
        for i, (data, mime) in enumerate(images, start=1):
            parts.append({"text": f"Page {i} :"})
            parts.append(
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}}
            )
        parts.append({"text": user_text})
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

    async def generate(self, system, user_text, images, *, max_tokens=4096, purpose="analyze"):
        url = f"{BASE_URL}/{self.cfg.model}:generateContent"
        headers = {"x-goog-api-key": self.cfg.api_key or "", "content-type": "application/json"}
        payload = self.build_payload(system, user_text, images, max_tokens)
        resp = None
        for attempt in range(1):
            try:
                resp = await asyncio.to_thread(
                    self.http.request, "POST", url, headers=headers, json=payload, timeout=self.timeout
                )
            except HttpError as exc:
                if attempt == 2:
                    raise ProviderError(f"gemini: {exc}", retryable=True) from None
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status in (429, 500, 502, 503) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break
        assert resp is not None
        if not resp.ok:
            try:
                details = resp.json().get("error", {}).get("message", "")
            except (ValueError, AttributeError):
                details = ""

            raise ProviderError(
                f"gemini: HTTP {resp.status} - {details}",
                retryable=resp.status in (429, 500, 502, 503),
            )
        try:
            data = resp.json()
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
            usage = data.get("usageMetadata", {})
            return LLMResult(
                text,
                int(usage.get("promptTokenCount", 0)),
                int(usage.get("candidatesTokenCount", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("gemini: rÃ©ponse vide ou bloquÃ©e") from None

