"""Machine à états d'une conversation StudyLens.

Interaction 100 % silencieuse : l'utilisateur envoie des photos (depuis les
lunettes) ; l'analyse démarre seule après AUTO_ANALYZE_DELAY secondes sans
nouvelle photo. Les commandes texte (fin, annule, supprime, qN, aide) sont
facultatives et ne servent que si une saisie silencieuse est disponible.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections import defaultdict

from .ai.base import Provider
from .budget import Budget, BudgetExceeded
from .answer_images import render_answer, should_render
from .config import Settings
from .imaging import ImageError, process_image
from .messenger import DownloadError, IncomingMessage, MessengerClient
from .models import ANALYZING, COLLECTING, DONE
from .pipeline import AnalysisError, analyze_session
from .screens import format_question
from .store import Store

log = logging.getLogger("studylens.orchestrator")

CMD_FINISH = {"fin", "analyse", "analyser", "go"}
CMD_UNDO = {"annule", "annuler", "retour"}
CMD_DELETE = {"supprime", "supprimer", "efface", "effacer", "stop"}
CMD_HELP = {"aide", "help", "?"}

HELP = (
    "StudyLens\n"
    "Envoyez vos pages en photo : l'analyse démarre seule.\n"
    "Commandes (facultatives) : fin · annule (dernière page) · supprime (tout) · q3 (revoir la question 3)"
)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        messenger: MessengerClient,
        providers: list[Provider],
        budget: Budget,
    ):
        self.s = settings
        self.store = store
        self.msg = messenger
        self.providers = providers
        self.budget = budget
        self._timers: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ entrée
    async def handle_message(self, ev: IncomingMessage) -> None:
        if not self.store.first_time_mid(ev.mid):
            return
        if not self._authorized(ev.psid):
            log.warning("expéditeur non autorisé psid=%s", ev.psid)
            await self.msg.send_text(ev.psid, "Accès non autorisé.")
            return
        async with self._locks[ev.psid]:
            if ev.image_urls:
                await self._on_images(ev)
            elif ev.text:
                await self._on_text(ev)

    def _authorized(self, psid: str) -> bool:
        return psid in self.s.allowed_psids

    # ------------------------------------------------------------ photos
    async def _on_images(self, ev: IncomingMessage) -> None:
        psid = ev.psid
        session = self.store.latest_session(psid)
        if session and session.state == ANALYZING:
            await self.msg.send_text(psid, "Analyse en cours, patientez.")
            return
        if session is None or session.state == DONE:
            day_start = time.time() - 86400
            if self.store.count_sessions_since(psid, day_start) >= self.s.max_sessions_per_day:
                await self.msg.send_text(psid, "Limite de sessions atteinte pour aujourd'hui.")
                return
            session = self.store.create_session(psid)

        captured = 0
        for url in ev.image_urls:
            n = self.store.page_count(session.id)
            if n >= self.s.max_pages:
                await self.msg.send_text(psid, f"Limite de {self.s.max_pages} pages atteinte.")
                break
            try:
                raw = await self.msg.download_image(url)
            except DownloadError:
                await self.msg.send_text(psid, "Photo non reçue, renvoyez-la.")
                continue
            try:
                img = await asyncio.to_thread(
                    process_image,
                    raw,
                    max_side=self.s.image_max_side,
                    min_side=self.s.image_min_side,
                    blur_threshold=self.s.blur_threshold,
                )
            except ImageError:
                await self.msg.send_text(psid, "Image illisible, reprenez la photo.")
                continue
            if img.is_blurry:
                await self.msg.send_text(psid, f"⚠ Photo floue : reprenez la page {n + 1}.")
                continue
            self.store.add_page(session.id, img.data, img.width, img.height, img.blur_score)
            captured += 1
            await self.msg.send_text(psid, f"Page {n + 1} capturée")

        if captured:
            self._schedule(psid, session.id, self.s.auto_analyze_delay)

    # ------------------------------------------------------------ texte
    async def _on_text(self, ev: IncomingMessage) -> None:
        psid, cmd = ev.psid, _norm(ev.text)
        session = self.store.latest_session(psid)

        if cmd in CMD_HELP:
            await self.msg.send_text(psid, HELP)
        elif cmd in CMD_FINISH:
            if session and session.state == COLLECTING and self.store.page_count(session.id):
                self._cancel_timer(psid)
                self._spawn(self.analyze(psid, session.id))
            else:
                await self.msg.send_text(psid, "Aucune page à analyser.")
        elif cmd in CMD_UNDO:
            if session and session.state == COLLECTING and self.store.delete_last_page(session.id):
                n = self.store.page_count(session.id)
                await self.msg.send_text(psid, f"Dernière page supprimée ({n} restante(s)).")
                if n == 0:
                    self._cancel_timer(psid)
                else:
                    self._schedule(psid, session.id, self.s.auto_analyze_delay)
            else:
                await self.msg.send_text(psid, "Rien à annuler.")
        elif cmd in CMD_DELETE:
            if session:
                self._cancel_timer(psid)
                self.store.delete_session(session.id)
                await self.msg.send_text(psid, "Session et photos supprimées.")
            else:
                await self.msg.send_text(psid, "Aucune session.")
        elif (m := re.fullmatch(r"q\s*(\d{1,3})", cmd)):
            await self._resend_question(psid, session, int(m.group(1)))
        else:
            await self.msg.send_text(psid, "Envoyez des photos, ou 'aide'.")

    async def _resend_question(self, psid: str, session, number: int) -> None:
        questions = self.store.get_results(session.id) if session and session.state == DONE else []
        if not 1 <= number <= len(questions):
            await self.msg.send_text(psid, "Question introuvable.")
            return
        for m in format_question(questions[number - 1], number, len(questions), self.s.screen_chars, self.s.question_chars):
            await self.msg.send_text(psid, m)

    # ------------------------------------------------------------ planification
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _cancel_timer(self, psid: str) -> None:
        t = self._timers.pop(psid, None)
        if t and not t.done():
            t.cancel()

    def _schedule(self, psid: str, sid: int, delay: float, attempt: int = 0) -> None:
        self._cancel_timer(psid)

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
                await self.analyze(psid, sid, attempt)
            except asyncio.CancelledError:
                raise
            except Exception:  # filet de sécurité : jamais d'exception silencieuse
                log.exception("erreur inattendue pendant l'analyse")

        self._timers[psid] = self._spawn(run())

    async def wait_idle(self) -> None:
        """Attend la fin des tâches en cours (utile aux tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        """Arrêt propre : annule les minuteurs d'analyse en attente."""
        for psid in list(self._timers):
            self._cancel_timer(psid)
        await self.wait_idle()

    # ------------------------------------------------------------ analyse
    async def analyze(self, psid: str, sid: int, attempt: int = 0) -> None:
        async with self._locks[psid]:
            session = self.store.get_session(sid)
            pages = self.store.pages(sid) if session else []
            if not session or session.state != COLLECTING or not pages:
                return
            self.store.set_state(sid, ANALYZING)

        await self.msg.send_text(psid, f"Analyse en cours… ({len(pages)} page(s))")
        try:
            images = [(self.store.read_page(p), "image/jpeg") for p in pages]
            result = await analyze_session(self.providers, images, self.s, self.budget)
        except BudgetExceeded as exc:
            self.store.set_state(sid, COLLECTING)
            await self.msg.send_text(psid, f"Analyse suspendue : {exc}. Vos pages sont conservées.")
            return
        except AnalysisError as exc:
            self.store.set_state(sid, COLLECTING)
            log.warning("analyse échouée: %s", exc)
            if attempt < self.s.auto_retries:
                await self.msg.send_text(
                    psid, f"Échec temporaire. Nouvelle tentative dans {int(self.s.retry_delay)} s."
                )
                self._schedule(psid, sid, self.s.retry_delay, attempt + 1)
            else:
                await self.msg.send_text(
                    psid, "Analyse impossible pour le moment. Vos pages sont conservées : envoyez 'fin' pour réessayer."
                )
            return
        except Exception:
            self.store.set_state(sid, COLLECTING)
            log.exception("erreur inattendue")
            await self.msg.send_text(psid, "Erreur inattendue. Vos pages sont conservées.")
            return

        self.store.save_results(sid, result.questions)
        self.store.set_state(sid, DONE)
        if self.s.delete_images_after_analysis:
            self.store.delete_session_images(sid)
        await self._send_results(psid, result)

    async def _send_results(self, psid: str, result) -> None:
        qs = result.questions
        if not qs:
            await self.msg.send_text(psid, "Aucune question détectée dans ces pages.")
        else:
            head = f"{len(qs)} question(s) détectée(s)"
            if result.unreadable_pages:
                pages = ", ".join(str(p) for p in result.unreadable_pages)
                head += f"\n⚠ Page(s) illisible(s) : {pages}. Reprenez-les."
            await self.msg.send_text(psid, head)
            for i, q in enumerate(qs, start=1):
                # Pour les consignes de schéma/tableau, envoyer aussi une image
                # lisible générée localement, sans appel supplémentaire à Groq.
                if should_render(q.text, q.answer):
                    try:
                        png = await asyncio.to_thread(render_answer, q.text, q.answer)
                        sent = await self.msg.send_image(psid, png)
                        if sent:
                            await self.msg.send_text(psid, f"Q{i}/{len(qs)} — résultat visuel envoyé.")
                    except Exception:
                        log.exception("rendu visuel impossible pour Q%s", i)
                for m in format_question(q, i, len(qs), self.s.screen_chars, self.s.question_chars):
                    await self.msg.send_text(psid, m)
        for w in result.warnings:
            await self.msg.send_text(psid, f"⚠ {w}")
