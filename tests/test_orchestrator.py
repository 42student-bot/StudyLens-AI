import asyncio
import json
import os

from app.ai.mock import MockProvider, default_analysis
from app.budget import Budget
from app.config import Settings
from app.http import HttpResponse
from app.messenger import IncomingMessage, MessengerClient
from app.models import DONE
from app.orchestrator import Orchestrator
from app.store import Store

from conftest import FakeHttp, make_page_jpeg

run = asyncio.run
PSID = "42"
CDN = "https://scontent.xx.fbcdn.net/"


class Env:
    def __init__(self, tmp_path, providers=None, **kw):
        base = dict(
            data_dir=str(tmp_path), fb_page_token="PAGE_TOKEN", allowed_psids=frozenset({PSID}),
            auto_analyze_delay=0.05, retry_delay=0.05, budget_daily=100.0, budget_monthly=100.0,
        )
        base.update(kw)
        self.s = Settings(**base)
        self.http = FakeHttp()
        self.sent_ok = True
        self.http.when(lambda m, u: "graph.facebook.com" in u, lambda m, u, j: HttpResponse(200 if self.sent_ok else 500, b"{}"))
        self.images: dict[str, bytes] = {}
        self.http.when(lambda m, u: m == "GET" and u.startswith(CDN), self._serve)
        self.store = Store(str(tmp_path))
        self.provider = providers if providers is not None else [MockProvider(self._responder)]
        self.orch = Orchestrator(self.s, self.store, MessengerClient(self.s, self.http), self.provider, Budget(self.s, self.store))
        self.mid = 0
        self.analysis = None

    def _serve(self, m, u, j):
        if u in self.images:
            return HttpResponse(200, self.images[u], {"content-type": "image/jpeg"})
        return HttpResponse(404, b"")

    def _responder(self, purpose, n):
        if purpose == "verify":
            return '{"corrections": []}'
        d = self.analysis or default_analysis(n)
        return json.dumps(d)

    def add_image(self, name, data):
        url = CDN + name
        self.images[url] = data
        return url

    def texts(self):
        return [r["json"]["message"]["text"] for r in self.http.requests if "graph.facebook.com" in r["url"]]

    def send(self, text="", urls=(), psid=PSID, mid=None):
        self.mid += 1
        run_coro = self.orch.handle_message(IncomingMessage(psid, mid or f"m{self.mid}", text, list(urls)))
        return run_coro


def scenario(env, coro_fn):
    async def main():
        await coro_fn()
        if env.s.auto_analyze_delay > 10:  # minuteur volontairement très long : on l'annule
            await env.orch.shutdown()
        else:
            await env.orch.wait_idle()
    run(main())


def test_full_flow_multi_page_auto_analysis(tmp_path, sharp_jpeg):
    env = Env(tmp_path)
    env.analysis = {**default_analysis(3), "questions": [
        {"numero": "1", "enonce": "Rôle du DNS ?", "reponse": "Traduit un nom en IP.", "source": "Page 2", "origine": "document", "confiance": "haute", "ambigue": False, "note": "", "calculs": []},
        {"numero": "2", "enonce": "Calcul", "reponse": "42 W", "source": "Pages 1 et 3", "origine": "mixte", "confiance": "moyenne", "ambigue": False, "note": "", "calculs": [{"expression": "12*3.5", "resultat": "42"}]},
    ]}
    urls = [env.add_image(f"p{i}.jpg", make_page_jpeg(True, seed=i)) for i in range(3)]

    async def go():
        await env.send(urls=urls[:2])       # 2 photos dans un message
        await env.send(urls=urls[2:])       # puis une 3e
    scenario(env, go)

    t = env.texts()
    assert t[:3] == ["Page 1 capturée", "Page 2 capturée", "Page 3 capturée"]
    assert "Analyse en cours… (3 page(s))" in t
    assert "2 question(s) détectée(s)" in t
    assert any(x.startswith("Q1/2") and "Rôle du DNS" in x and "Page 2" in x for x in t)
    assert any(x.startswith("Q2/2") and "42 W" in x for x in t)
    # tous les envois portent le jeton en en-tête et jamais dans l'URL
    sends = [r for r in env.http.requests if "graph.facebook.com" in r["url"]]
    assert all(r["headers"]["Authorization"] == "Bearer PAGE_TOKEN" and "PAGE_TOKEN" not in r["url"] for r in sends)
    # UNE seule analyse pour les 3 pages, après la dernière photo
    p = env.provider[0]
    assert [c["purpose"] for c in p.calls] == ["analyze", "verify"] and p.calls[0]["n_images"] == 3
    # état, résultats conservés, images supprimées (par défaut)
    s = env.store.latest_session(PSID)
    assert s.state == DONE and len(env.store.get_results(s.id)) == 2
    assert env.store.pages(s.id) == [] and not os.path.exists(os.path.join(env.store.images_dir, str(s.id)))


def test_blurry_photo_rejected_and_not_counted(tmp_path, sharp_jpeg, blurry_jpeg):
    env = Env(tmp_path)
    u1, u2, u3 = env.add_image("a.jpg", sharp_jpeg), env.add_image("b.jpg", blurry_jpeg), env.add_image("c.jpg", make_page_jpeg(True, seed=9))

    async def go():
        await env.send(urls=[u1, u2, u3])
    scenario(env, go)
    t = env.texts()
    assert "Page 1 capturée" in t and "⚠ Photo floue : reprenez la page 2." in t and "Page 2 capturée" in t
    assert env.provider[0].calls[0]["n_images"] == 2  # la floue n'a pas été envoyée à l'IA


def test_unauthorized_sender_gets_nothing_processed(tmp_path, sharp_jpeg):
    env = Env(tmp_path)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u], psid="666")
    scenario(env, go)
    assert env.texts() == ["Accès non autorisé."]
    assert env.provider[0].calls == [] and env.store.latest_session("666") is None
    assert not any(r["method"] == "GET" for r in env.http.requests)  # image jamais téléchargée


def test_ssrf_and_bad_downloads(tmp_path, sharp_jpeg):
    env = Env(tmp_path)

    async def go():
        await env.send(urls=["https://evil.example.com/a.jpg", "http://169.254.169.254/x", CDN + "absent.jpg"])
    scenario(env, go)
    assert not any(r["method"] == "GET" and "evil" in r["url"] or "169.254" in r["url"] for r in env.http.requests)
    assert env.texts().count("Photo non reçue, renvoyez-la.") == 3
    assert env.provider[0].calls == []


def test_duplicate_webhook_delivery_is_ignored(tmp_path, sharp_jpeg):
    env = Env(tmp_path)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u], mid="SAME")
        await env.send(urls=[u], mid="SAME")
    scenario(env, go)
    assert env.texts().count("Page 1 capturée") == 1 and env.texts().count("Page 2 capturée") == 0


def test_commands_undo_finish_delete_and_resend(tmp_path):
    env = Env(tmp_path, auto_analyze_delay=999.0)
    urls = [env.add_image(f"p{i}.jpg", make_page_jpeg(True, seed=i)) for i in range(2)]
    env.analysis = {**default_analysis(1), "questions": [
        {"numero": "1", "enonce": "Q1", "reponse": "R1", "source": "", "origine": "general", "confiance": "haute", "ambigue": False, "note": "", "calculs": []}]}

    async def go():
        await env.send(urls=urls)
        await env.send("Annule")                 # insensible à la casse
        sid = env.store.latest_session(PSID).id
        assert env.store.page_count(sid) == 1
        await env.send("FIN")                    # lance l'analyse sans attendre 999 s
        await env.orch.wait_idle() if False else None
        for _ in range(100):
            if env.store.latest_session(PSID).state == DONE:
                break
            await asyncio.sleep(0.02)
        await env.send("q1")
        await env.send("q9")
        await env.send("supprime")
        await env.send("n'importe quoi")
    scenario(env, go)
    t = env.texts()
    assert "Dernière page supprimée (1 restante(s))." in t
    assert t.count("1 question(s) détectée(s)") == 1
    assert sum(1 for x in t if x.startswith("Q1/1")) == 2  # envoi initial + « q1 »
    assert "Question introuvable." in t and "Session et photos supprimées." in t
    assert "Envoyez des photos, ou 'aide'." in t
    assert env.store.latest_session(PSID) is None
    assert env.provider[0].calls[0]["n_images"] == 1


def test_provider_failure_keeps_pages_then_retries_then_succeeds(tmp_path, sharp_jpeg):
    flaky = MockProvider(fail_times=1)
    env = Env(tmp_path, providers=[flaky], auto_retries=2)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
        for _ in range(200):
            await asyncio.sleep(0.02)
            if env.store.latest_session(PSID).state == DONE:
                break
    scenario(env, go)
    t = env.texts()
    assert any(x.startswith("Échec temporaire") for x in t)
    assert "1 question(s) détectée(s)" in t  # 2e tentative réussie
    assert env.store.latest_session(PSID).state == DONE


def test_provider_failure_exhausts_retries_pages_kept(tmp_path, sharp_jpeg):
    env = Env(tmp_path, providers=[MockProvider(fail_times=99)], auto_retries=1)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
        for _ in range(200):
            await asyncio.sleep(0.02)
            if any(x.startswith("Analyse impossible") for x in env.texts()):
                break
    scenario(env, go)
    s = env.store.latest_session(PSID)
    assert s.state == "collecting" and env.store.page_count(s.id) == 1  # rien n'est perdu
    assert any(x.startswith("Analyse impossible") for x in env.texts())


def test_budget_block_message(tmp_path, sharp_jpeg):
    paid = MockProvider(paid=True, price_in=1000.0, price_out=1000.0)
    env = Env(tmp_path, providers=[paid], budget_daily=0.1)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
    scenario(env, go)
    assert any(x.startswith("Analyse suspendue") and "budget" in x for x in env.texts())
    assert paid.calls == [] and env.store.page_count(env.store.latest_session(PSID).id) == 1


def test_limits_max_pages_and_sessions_per_day(tmp_path):
    env = Env(tmp_path, max_pages=2, auto_analyze_delay=999.0, max_sessions_per_day=1)
    urls = [env.add_image(f"p{i}.jpg", make_page_jpeg(True, seed=i)) for i in range(3)]

    async def go():
        await env.send(urls=urls)
        await env.send("supprime")
        await env.send(urls=urls[:1])  # 2e session du jour (la 1re a été supprimée -> compteur repart de 0)
    scenario(env, go)
    assert "Limite de 2 pages atteinte." in env.texts()

    env2 = Env(tmp_path / "x", max_sessions_per_day=1, auto_analyze_delay=0.01) if (tmp_path / "x").mkdir() is None else None
    u = env2.add_image("a.jpg", make_page_jpeg(True))

    async def go2():
        await env2.send(urls=[u])
        for _ in range(100):
            await asyncio.sleep(0.02)
            if env2.store.latest_session(PSID).state == DONE:
                break
        await env2.send(urls=[u])  # nouvelle session refusée
    scenario(env2, go2)
    assert "Limite de sessions atteinte pour aujourd'hui." in env2.texts()


def test_new_photo_after_done_starts_new_session(tmp_path, sharp_jpeg):
    env = Env(tmp_path)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
        for _ in range(100):
            await asyncio.sleep(0.02)
            if env.store.latest_session(PSID).state == DONE:
                break
        first = env.store.latest_session(PSID).id
        await env.send(urls=[u])
        assert env.store.latest_session(PSID).id != first
    scenario(env, go)
    assert env.texts().count("Page 1 capturée") == 2


def test_send_failure_does_not_crash_analysis(tmp_path, sharp_jpeg):
    env = Env(tmp_path)
    env.sent_ok = False
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
    scenario(env, go)  # ne doit pas lever
    assert env.store.latest_session(PSID).state == DONE


def test_purge_removes_old_images_and_sessions(tmp_path, sharp_jpeg):
    env = Env(tmp_path, auto_analyze_delay=999.0)
    u = env.add_image("a.jpg", sharp_jpeg)

    async def go():
        await env.send(urls=[u])
    scenario(env, go)
    sid = env.store.latest_session(PSID).id
    assert env.store.purge(image_ttl=3600, session_ttl=3600, keep_history=False) == {"images": 0, "sessions": 0}
    r = env.store.purge(image_ttl=-1, session_ttl=-1, keep_history=False)
    assert r == {"images": 1, "sessions": 1}
    assert env.store.get_session(sid) is None and not os.path.exists(os.path.join(env.store.images_dir, str(sid)))
