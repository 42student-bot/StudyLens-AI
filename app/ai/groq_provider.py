from __future__ import annotations

import asyncio
import base64

from ..config import ProviderConfig
from ..http import HttpClient, HttpError
from .base import Image, LLMResult, Provider, ProviderError

API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(Provider):
    name = "groq"

    def __init__(self, cfg: ProviderConfig, http: HttpClient, timeout: float = 120.0):
        self.cfg = cfg
        self.http = http
        self.timeout = timeout
        self.free_tier = cfg.free_tier
        self.price_in = cfg.price_in
        self.price_out = cfg.price_out

    def build_payload(self, system, user_text, images: list[Image], max_tokens) -> dict:
        content: list[dict] = []

        for i, (data, mime) in enumerate(images, start=1):
            content.append({"type": "text", "text": f"Page {i} :"})
            encoded = base64.b64encode(data).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime};base64,{encoded}"
                },
            })

        content.append({"type": "text", "text": user_text})

        return {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

    async def generate(
        self, system, user_text, images, *, max_tokens=4096, purpose="analyze"
    ):
        payload = self.build_payload(system, user_text, images, max_tokens)
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key or ''}",
            "Content-Type": "application/json",
        }

        resp = None
        for attempt in range(3):
            try:
                resp = await asyncio.to_thread(
                    self.http.request,
                    "POST",
                    API_URL,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
            except HttpError as exc:
                if attempt == 2:
                    raise ProviderError(f"groq: {exc}", retryable=True) from None
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if resp.status == 429:
                try:
                    details = resp.json().get("error", {}).get("message", "")
                except (ValueError, AttributeError):
                    details = ""
                raise ProviderError(
                    f"groq: HTTP 429 - {details}",
                    retryable=False,
                )

            if resp.status in (500, 502, 503) and attempt < 2:
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
                f"groq: HTTP {resp.status} - {details}",
                retryable=resp.status in (429, 500, 502, 503),
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            usage = data.get("usage", {})

            if not isinstance(text, str) or not text.strip():
                raise ValueError("contenu vide")

            return LLMResult(
                text,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("groq: réponse vide ou inattendue") from None