"""Configuration lue depuis les variables d'environnement.

Aucun secret n'est codé en dur : tout vient de l'environnement (fichier .env
chargé par systemd via EnvironmentFile).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "oui", "on"}


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _float_opt(env: Mapping[str, str], name: str) -> float | None:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _csv(env: Mapping[str, str], name: str, default: str = "") -> tuple[str, ...]:
    raw = env.get(name, default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class ProviderConfig:
    """Paramètres d'un fournisseur IA.

    price_in / price_out : prix par million de tokens, dans la devise de votre
    choix (la même que BUDGET_DAILY / BUDGET_MONTHLY). À renseigner vous-même
    d'après la grille tarifaire officielle : le code ne devine aucun prix.
    """

    name: str
    api_key: str | None
    model: str
    free_tier: bool = False
    price_in: float | None = None
    price_out: float | None = None


@dataclass(frozen=True)
class Settings:
    # --- stockage ---
    data_dir: str = "./data"

    # --- Meta / Messenger ---
    fb_app_secret: str = ""
    fb_page_token: str = ""
    fb_verify_token: str = ""
    graph_api_version: str = ""  # vide = URL non versionnée (voir README)
    allowed_psids: frozenset[str] = frozenset()
    image_host_suffixes: tuple[str, ...] = (
        ".fbcdn.net",
        ".fbsbx.com",
        ".facebook.com",
        ".cdninstagram.com",
    )

    # --- comportement de session ---
    auto_analyze_delay: float = 25.0  # secondes sans nouvelle photo avant analyse
    auto_retries: int = 2
    retry_delay: float = 60.0
    max_pages: int = 20
    max_image_bytes: int = 12 * 1024 * 1024
    max_sessions_per_day: int = 30

    # --- affichage ---
    screen_chars: int = 450  # taille max d'un message ; à ajuster après test réel
    question_chars: int = 160

    # --- images ---
    image_max_side: int = 2000
    image_min_side: int = 300
    blur_threshold: float = 60.0  # variance du laplacien ; à calibrer sur vos photos

    # --- confidentialité ---
    delete_images_after_analysis: bool = True
    image_retention_minutes: int = 120
    session_retention_hours: int = 48
    keep_history: bool = False  # False : résultats supprimés à l'échéance

    # --- IA ---
    primary_provider: str = "gemini"
    fallback_provider: str = ""
    allow_paid_providers: bool = False
    max_output_tokens: int = 4096
    image_tokens_estimate: int = 1600  # sert uniquement à estimer le coût
    verify_with_llm: bool = True
    verify_delay_seconds: float = 35.0
    request_timeout: float = 120.0
    providers: Mapping[str, ProviderConfig] = field(default_factory=dict)

    # --- budget (unité = celle des prix fournisseurs) ---
    budget_daily: float = 0.50
    budget_monthly: float = 5.00
    max_requests_per_day: int = 120

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        providers = {
            "groq": ProviderConfig(
                name="groq",
                api_key=e.get("GROQ_API_KEY") or None,
                model=e.get("GROQ_MODEL", "qwen/qwen3.8-27b"),
                free_tier=_bool(e, "GROQ_FREE_TIER", False),
                price_in=_float_opt(e, "GROQ_PRICE_IN_PER_MTOK"),
                price_out=_float_opt(e, "GROQ_PRICE_OUT_PER_MTOK"),
            ),
            "anthropic": ProviderConfig(
                name="anthropic",
                api_key=e.get("ANTHROPIC_API_KEY") or None,
                model=e.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                free_tier=False,
                price_in=_float_opt(e, "ANTHROPIC_PRICE_IN_PER_MTOK"),
                price_out=_float_opt(e, "ANTHROPIC_PRICE_OUT_PER_MTOK"),
            ),
            "gemini": ProviderConfig(
                name="gemini",
                api_key=e.get("GEMINI_API_KEY") or None,
                # À vérifier : nom du modèle et quota gratuit actuels.
                model=e.get("GEMINI_MODEL", "gemini-2.5-flash"),
                free_tier=_bool(e, "GEMINI_FREE_TIER", False),
                price_in=_float_opt(e, "GEMINI_PRICE_IN_PER_MTOK"),
                price_out=_float_opt(e, "GEMINI_PRICE_OUT_PER_MTOK"),
            ),
        }
        return Settings(
            data_dir=e.get("DATA_DIR", "./data"),
            fb_app_secret=e.get("FB_APP_SECRET", ""),
            fb_page_token=e.get("FB_PAGE_TOKEN", ""),
            fb_verify_token=e.get("FB_VERIFY_TOKEN", ""),
            graph_api_version=e.get("GRAPH_API_VERSION", ""),
            allowed_psids=frozenset(_csv(e, "ALLOWED_PSIDS")),
            image_host_suffixes=_csv(
                e,
                "IMAGE_HOST_SUFFIXES",
                ".fbcdn.net,.fbsbx.com,.facebook.com,.cdninstagram.com",
            ),
            auto_analyze_delay=_float(e, "AUTO_ANALYZE_DELAY", 25.0),
            auto_retries=_int(e, "AUTO_RETRIES", 2),
            retry_delay=_float(e, "RETRY_DELAY", 60.0),
            max_pages=_int(e, "MAX_PAGES", 20),
            max_image_bytes=_int(e, "MAX_IMAGE_BYTES", 12 * 1024 * 1024),
            max_sessions_per_day=_int(e, "MAX_SESSIONS_PER_DAY", 30),
            screen_chars=_int(e, "SCREEN_CHARS", 450),
            question_chars=_int(e, "QUESTION_CHARS", 160),
            image_max_side=_int(e, "IMAGE_MAX_SIDE", 2000),
            image_min_side=_int(e, "IMAGE_MIN_SIDE", 300),
            blur_threshold=_float(e, "BLUR_THRESHOLD", 60.0),
            delete_images_after_analysis=_bool(e, "DELETE_IMAGES_AFTER_ANALYSIS", True),
            image_retention_minutes=_int(e, "IMAGE_RETENTION_MINUTES", 120),
            session_retention_hours=_int(e, "SESSION_RETENTION_HOURS", 48),
            keep_history=_bool(e, "KEEP_HISTORY", False),
            primary_provider=e.get("PRIMARY_PROVIDER", "gemini"),
            fallback_provider=e.get("FALLBACK_PROVIDER", ""),
            allow_paid_providers=_bool(e, "ALLOW_PAID_PROVIDERS", False),
            max_output_tokens=_int(e, "MAX_OUTPUT_TOKENS", 4096),
            image_tokens_estimate=_int(e, "IMAGE_TOKENS_ESTIMATE", 1600),
            verify_with_llm=_bool(e, "VERIFY_WITH_LLM", True),
            verify_delay_seconds=_float(e, "VERIFY_DELAY_SECONDS", 35.0),
            request_timeout=_float(e, "REQUEST_TIMEOUT", 120.0),
            providers=providers,
            budget_daily=_float(e, "BUDGET_DAILY", 0.50),
            budget_monthly=_float(e, "BUDGET_MONTHLY", 5.00),
            max_requests_per_day=_int(e, "MAX_REQUESTS_PER_DAY", 120),
        )

    def graph_url(self, path: str) -> str:
        base = "https://graph.facebook.com"
        if self.graph_api_version:
            base += "/" + self.graph_api_version.strip("/")
        return f"{base}/{path.lstrip('/')}"
