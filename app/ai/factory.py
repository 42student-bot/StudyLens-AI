"""Construction de la chaîne de fournisseurs (principal puis secours)."""
from __future__ import annotations

import logging

from ..config import Settings
from ..http import HttpClient
from .anthropic_provider import AnthropicProvider
from .base import Provider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .mock import MockProvider

log = logging.getLogger("studylens.ai")


def build_provider(name: str, settings: Settings, http: HttpClient) -> Provider | None:
    """Retourne le fournisseur s'il est utilisable, sinon None (avec la raison en log).

    Règle : aucun appel payant sans clé, sans autorisation explicite
    (ALLOW_PAID_PROVIDERS=true) et sans prix déclarés pour le calcul du budget.
    """
    name = name.strip().lower()
    if not name:
        return None
    if name == "mock":
        return MockProvider()
    cfg = settings.providers.get(name)
    if cfg is None:
        log.error("fournisseur inconnu: %s", name)
        return None
    if not cfg.api_key:
        log.warning("fournisseur %s ignoré : clé API absente", name)
        return None
    paid = not cfg.free_tier
    if paid:
        if not settings.allow_paid_providers:
            log.warning("fournisseur %s ignoré : payant et ALLOW_PAID_PROVIDERS=false", name)
            return None
        if cfg.price_in is None or cfg.price_out is None:
            log.warning("fournisseur %s ignoré : prix par Mtok non déclarés (budget impossible)", name)
            return None
    if name == "anthropic":
        return AnthropicProvider(cfg, http, settings.request_timeout)
    if name == "gemini":
        return GeminiProvider(cfg, http, settings.request_timeout)
    if name == "groq":
        return GroqProvider(cfg, http, settings.request_timeout)
    return None


def build_chain(settings: Settings, http: HttpClient) -> list[Provider]:
    chain: list[Provider] = []
    for name in (settings.primary_provider, settings.fallback_provider):
        p = build_provider(name, settings, http)
        if p is not None and all(p.name != x.name for x in chain):
            chain.append(p)
    return chain
