"""Fournisseur factice pour les tests et la démonstration hors ligne.

Ses réponses sont des données de test et ne répondent PAS réellement aux
documents. Ne jamais l'utiliser comme fournisseur de production.
"""
from __future__ import annotations

import json
from typing import Callable

from .base import Image, LLMResult, Provider, ProviderError


def default_analysis(n_pages: int) -> dict:
    return {
        "pages": [
            {"page": i, "lisible": True, "type": "texte", "transcription": f"[MOCK] contenu page {i}"}
            for i in range(1, n_pages + 1)
        ],
        "questions": [
            {
                "numero": "1",
                "enonce": "[MOCK] Question 1 ?",
                "reponse": "[MOCK] Réponse de test.",
                "source": "Page 1",
                "origine": "document",
                "confiance": "haute",
                "ambigue": False,
                "note": "",
                "calculs": [],
            }
        ],
    }


class MockProvider(Provider):
    name = "mock"
    free_tier = True

    def __init__(
        self,
        responder: Callable[[str, int], str] | None = None,
        fail_times: int = 0,
        name: str = "mock",
        paid: bool = False,
        price_in: float | None = None,
        price_out: float | None = None,
        tokens: tuple[int, int] = (100, 50),
    ):
        self.responder = responder
        self.fail_times = fail_times
        self.name = name
        self.free_tier = not paid
        self.price_in = price_in
        self.price_out = price_out
        self.tokens = tokens
        self.calls: list[dict] = []

    async def generate(self, system, user_text, images: list[Image], *, max_tokens=4096, purpose="analyze"):
        self.calls.append({"purpose": purpose, "n_images": len(images), "user_text": user_text})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ProviderError("échec simulé", retryable=True)
        if self.responder is not None:
            text = self.responder(purpose, len(images))
        elif purpose == "verify":
            text = json.dumps({"corrections": []})
        else:
            text = json.dumps(default_analysis(len(images)), ensure_ascii=False)
        return LLMResult(text, *self.tokens)
