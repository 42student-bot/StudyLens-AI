"""Fournisseur Anthropic (API Messages). Payant à l'usage."""
from __future__ import annotations

import asyncio
import base64

from ..config import ProviderConfig
from ..http import HttpClient, HttpError
from .base import Image, LLMResult, Provider, ProviderError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, cfg: ProviderConfig, http: HttpClient, timeout: float = 120.0):
        self.cfg = cfg
        self.http = http
        self.timeout = timeout
        self.free_tier = False
        self.price_in = cfg.price_in
        self.price_out = cfg.price_out

    def build_payload(self, system, user_text, images: list[Image], max_tokens) -> dict:
        content: list[dict] = []
        for i, (data, mime) in enumerate(images, start=1):
            content.append({"type": "text", "text": f"Page {i} :"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": user_text})
        return {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }

    async def generate(self, system, user_text, images, *, max_tokens=4096, purpose="analyze"):
        payload = self.build_payload(system, user_text, images, max_tokens)
        headers = {
            "x-api-key": self.cfg.api_key or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        resp = None
        for attempt in range(3):
            try:
                resp = await asyncio.to_thread(
                    self.http.request, "POST", API_URL, headers=headers, json=payload, timeout=self.timeout
                )
            except HttpError as exc:
                if attempt == 2:
                    raise ProviderError(f"anthropic: {exc}", retryable=True) from None
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status in (429, 500, 502, 503, 529) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break
        assert resp is not None
        if not resp.ok:
            raise ProviderError(
                f"anthropic: HTTP {resp.status}", retryable=resp.status in (429, 500, 502, 503, 529)
            )
        try:
            data = resp.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage", {})
            return LLMResult(text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        except (ValueError, KeyError, TypeError):
            raise ProviderError("anthropic: réponse inattendue") from None
