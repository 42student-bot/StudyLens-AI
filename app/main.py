"""Application ASGI (Starlette) : webhook Messenger + santé."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .ai.factory import build_chain
from .budget import Budget
from .config import Settings
from .http import HttpClient, RequestsHttp
from .messenger import MessengerClient, parse_events, verify_signature
from .orchestrator import Orchestrator
from .store import Store

log = logging.getLogger("studylens")

MAX_BODY = 1_000_000  # 1 Mo : un webhook Messenger est minuscule


def build_orchestrator(settings: Settings, http: HttpClient, store: Store) -> Orchestrator:
    providers = build_chain(settings, http)
    if not providers:
        log.error("AUCUN fournisseur IA utilisable : vérifiez clés, prix et ALLOW_PAID_PROVIDERS")
    return Orchestrator(settings, store, MessengerClient(settings, http), providers, Budget(settings, store))


def create_app(
    settings: Settings | None = None,
    http: HttpClient | None = None,
    orchestrator: Orchestrator | None = None,
    store: Store | None = None,
) -> Starlette:
    settings = settings or Settings.from_env()
    http = http or RequestsHttp()
    store = store or Store(settings.data_dir)
    orch = orchestrator or build_orchestrator(settings, http, store)

    async def purge_loop() -> None:
        while True:
            try:
                res = await asyncio.to_thread(
                    store.purge,
                    image_ttl=settings.image_retention_minutes * 60,
                    session_ttl=settings.session_retention_hours * 3600,
                    keep_history=settings.keep_history,
                )
                if res["images"] or res["sessions"]:
                    log.info("purge: %s", res)
            except Exception:
                log.exception("purge échouée")
            await asyncio.sleep(600)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        task = asyncio.create_task(purge_loop())
        try:
            yield
        finally:
            task.cancel()
            await orch.shutdown()

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "providers": [p.name for p in orch.providers]})

    async def webhook(request: Request) -> Response:
        if request.method == "GET":  # vérification d'abonnement par Meta
            q = request.query_params
            if (
                q.get("hub.mode") == "subscribe"
                and settings.fb_verify_token
                and q.get("hub.verify_token") == settings.fb_verify_token
            ):
                return PlainTextResponse(q.get("hub.challenge", ""))
            return PlainTextResponse("forbidden", status_code=403)

        body = await request.body()
        if len(body) > MAX_BODY:
            return PlainTextResponse("too large", status_code=413)
        if not verify_signature(settings.fb_app_secret, body, request.headers.get("x-hub-signature-256")):
            return PlainTextResponse("bad signature", status_code=403)
        try:
            payload = json.loads(body)
        except ValueError:
            return PlainTextResponse("bad json", status_code=400)
        # Répondre 200 tout de suite : Meta réessaie si la réponse tarde.
        for ev in parse_events(payload):
            orch._spawn(_safe(orch, ev))
        return PlainTextResponse("EVENT_RECEIVED")

    return Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/webhook", webhook, methods=["GET", "POST"]),
        ],
        lifespan=lifespan,
    )


async def _safe(orch: Orchestrator, ev) -> None:
    try:
        await orch.handle_message(ev)
    except Exception:
        log.exception("erreur de traitement d'un message")


def app_factory() -> Starlette:
    """Point d'entrée uvicorn : `uvicorn app.main:app_factory --factory`."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return create_app()
