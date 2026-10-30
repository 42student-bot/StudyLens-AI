import asyncio
import json

import pytest

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.base import ProviderError
from app.ai.factory import build_chain
from app.ai.gemini_provider import GeminiProvider
from app.ai.mock import MockProvider, default_analysis
from app.budget import Budget, BudgetExceeded
from app.config import ProviderConfig, Settings
from app.http import HttpError, HttpResponse
from app.pipeline import AnalysisError, analyze_session
from app.store import Store

from conftest import FakeHttp

run = asyncio.run
IMG = [(b"\xff\xd8fake", "image/jpeg")]


def settings(tmp_path, **kw):
    return Settings(data_dir=str(tmp_path), **kw)


def analysis(questions, pages=2):
    d = default_analysis(pages)
    d["questions"] = questions
    return json.dumps(d, ensure_ascii=False)


def q(num="1", text="Q ?", ans="R", **kw):
    base = {"numero": num, "enonce": text, "reponse": ans, "source": "Page 1",
            "origine": "document", "confiance": "haute", "ambigue": False, "note": "", "calculs": []}
    base.update(kw)
    return base


# ---------------------------------------------------------------- fournisseurs HTTP
def test_anthropic_request_and_parsing(tmp_path):
    http = FakeHttp()
    http.when(lambda m, u: "anthropic.com" in u, HttpResponse(200, json.dumps({
        "content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 1200, "output_tokens": 300}}).encode()))
    p = AnthropicProvider(ProviderConfig("anthropic", "sk-test", "claude-sonnet-5-5", price_in=3, price_out=15), http)
    res = run(p.generate("sys", "user", [(b"abc", "image/jpeg"), (b"def", "image/jpeg")], max_tokens=500))
    assert (res.text, res.input_tokens, res.output_tokens) == ("{}", 1200, 300)
    req = http.requests[0]
    assert req["headers"]["x-api-key"] == "sk-test" and "anthropic-version" in req["headers"]
    content = req["json"]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
    assert req["json"]["model"] == "claude-sonnet-5-5" and req["json"]["max_tokens"] == 500
    assert p.cost(1_000_000, 1_000_000) == 18


def test_gemini_request_and_parsing_key_in_header_not_url(tmp_path):
    http = FakeHttp()
    http.when(lambda m, u: "generativelanguage" in u, HttpResponse(200, json.dumps({
        "candidates": [{"content": {"parts": [{"text": "{\"a\":1}"}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}).encode()))
    p = GeminiProvider(ProviderConfig("gemini", "gk", "gemini-test", free_tier=True), http)
    res = run(p.generate("s", "u", [(b"x", "image/jpeg")]))
    assert res.text == '{"a":1}' and (res.input_tokens, res.output_tokens) == (10, 5)
    req = http.requests[0]
    assert "gk" not in req["url"] and req["headers"]["x-goog-api-key"] == "gk"
    assert req["json"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/jpeg"
    assert p.cost(10**6, 10**6) == 0.0  # palier gratuit déclaré


def test_provider_http_errors(monkeypatch):
    async def no_sleep(_): pass
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    http = FakeHttp()
    cfg = ProviderConfig("anthropic", "k", "m", price_in=1, price_out=1)
    p = AnthropicProvider(cfg, http)

    http.rules = [(lambda m, u: True, HttpResponse(401, b"no"))]
    with pytest.raises(ProviderError) as e:
        run(p.generate("s", "u", []))
    assert not e.value.retryable and len(http.requests) == 1  # pas de retry sur 401

    http.requests.clear()
    http.rules = [(lambda m, u: True, HttpResponse(429, b""))]
    with pytest.raises(ProviderError) as e:
        run(p.generate("s", "u", []))
    assert e.value.retryable and len(http.requests) == 3  # 3 tentatives

    http.rules = [(lambda m, u: True, HttpError("réseau", retryable=True))]
    with pytest.raises(ProviderError):
        run(p.generate("s", "u", []))

    http.rules = [(lambda m, u: True, HttpResponse(200, b'{"content": 5}'))]
    with pytest.raises(ProviderError):
        run(p.generate("s", "u", []))


def test_factory_enforces_paid_rules():
    http = FakeHttp()
    cfgs = {
        "anthropic": ProviderConfig("anthropic", "k", "m", price_in=3, price_out=15),
        "gemini": ProviderConfig("gemini", "k", "m", free_tier=True),
    }
    base = dict(providers=cfgs, primary_provider="gemini", fallback_provider="anthropic")
    # payant interdit par défaut
    assert [p.name for p in build_chain(Settings(**base), http)] == ["gemini"]
    # autorisé
    assert [p.name for p in build_chain(Settings(**base, allow_paid_providers=True), http)] == ["gemini", "anthropic"]
    # prix absents -> écarté même si autorisé
    cfgs2 = {**cfgs, "anthropic": ProviderConfig("anthropic", "k", "m")}
    assert [p.name for p in build_chain(Settings(**{**base, "providers": cfgs2}, allow_paid_providers=True), http)] == ["gemini"]
    # clé absente -> écarté
    cfgs3 = {**cfgs, "gemini": ProviderConfig("gemini", None, "m", free_tier=True)}
    assert build_chain(Settings(**{**base, "providers": cfgs3, "fallback_provider": ""}), http) == []
    # doublon principal == secours
    assert len(build_chain(Settings(**{**base, "fallback_provider": "gemini"}), http)) == 1


# ---------------------------------------------------------------- pipeline
def run_pipeline(tmp_path, provider_list, n_images=2, **kw):
    s = settings(tmp_path, **kw)
    store = Store(str(tmp_path))
    return run(analyze_session(provider_list, IMG * n_images, s, Budget(s, store))), store


def test_pipeline_multi_page_basic(tmp_path):
    p = MockProvider(lambda purpose, n: analysis([q("1"), q("2.a", "Calcul", "42", origine="general")]) if purpose == "analyze" else '{"corrections": []}')
    res, _ = run_pipeline(tmp_path, [p])
    assert [x.label for x in res.questions] == ["1", "2.a"]
    assert res.questions[1].origin == "general"
    assert p.calls[0]["n_images"] == 2  # les 2 pages dans UN seul appel
    assert [c["purpose"] for c in p.calls] == ["analyze", "verify"]
    assert p.calls[1]["n_images"] == 0  # la relecture n'envoie pas les images


def test_pipeline_handles_code_fences_and_bad_json_retry(tmp_path):
    outputs = iter(["Voici : pas du json", "```json\n" + analysis([q()]) + "\n```"])
    p = MockProvider(lambda purpose, n: next(outputs) if purpose == "analyze" else '{"corrections": []}')
    res, _ = run_pipeline(tmp_path, [p])
    assert len(res.questions) == 1
    assert [c["purpose"] for c in p.calls].count("analyze") == 2


def test_pipeline_persistent_bad_json_raises(tmp_path):
    p = MockProvider(lambda purpose, n: "nope")
    with pytest.raises(AnalysisError):
        run_pipeline(tmp_path, [p])


def test_pipeline_flags_wrong_calculation(tmp_path):
    bad = q("3", "Calculer P", "P = 40 W", calculs=[{"expression": "12*3.5", "resultat": "40"}])
    p = MockProvider(lambda purpose, n: analysis([bad]) if purpose == "analyze" else '{"corrections": []}')
    res, _ = run_pipeline(tmp_path, [p])
    a = res.questions[0]
    assert a.confidence == "faible" and "Recalcul" in a.answer and "= 42" in a.answer
    assert any("Calcul à revoir" in w for w in res.warnings)


def test_pipeline_unreadable_page_and_empty_answer_and_no_question(tmp_path):
    d = default_analysis(3)
    d["pages"][1]["lisible"] = False
    d["questions"] = [q("1", "Q", "")]
    p = MockProvider(lambda purpose, n: json.dumps(d) if purpose == "analyze" else '{"corrections": []}')
    res, _ = run_pipeline(tmp_path, [p], n_images=3)
    assert res.unreadable_pages == [2]
    assert res.questions[0].confidence == "faible" and "insuffisantes" in res.questions[0].answer

    p2 = MockProvider(lambda purpose, n: analysis([]))
    res2, _ = run_pipeline(tmp_path / "b", [p2]) if (tmp_path / "b").mkdir() is None else (None, None)
    assert res2.questions == []
    assert [c["purpose"] for c in p2.calls] == ["analyze"]  # rien à relire


def test_pipeline_llm_verification_applies_corrections_and_is_non_blocking(tmp_path):
    def responder(purpose, n):
        if purpose == "analyze":
            return analysis([q("1", "Q1", "faux"), q("2", "Q2", "ok")])
        return json.dumps({"corrections": [{"id": 1, "reponse": "juste", "confiance": "haute", "raison": "lu p.2"}, {"id": 99, "reponse": "x"}, "bizarre"]})
    p = MockProvider(responder)
    res, _ = run_pipeline(tmp_path, [p])
    assert res.questions[0].answer == "juste" and res.questions[0].note == "lu p.2"
    assert res.questions[1].answer == "ok"

    # relecture qui échoue : l'analyse reste valide
    def responder2(purpose, n):
        if purpose == "analyze":
            return analysis([q()])
        raise_me = "pas json"
        return raise_me
    res2, _ = run_pipeline(tmp_path / "c", [MockProvider(responder2)]) if (tmp_path / "c").mkdir() is None else (None, None)
    assert len(res2.questions) == 1 and "Relecture IA indisponible" in res2.warnings


def test_pipeline_fallback_when_primary_fails(tmp_path):
    primary = MockProvider(fail_times=99, name="primaire")
    backup = MockProvider(lambda purpose, n: analysis([q()]) if purpose == "analyze" else '{"corrections": []}', name="secours")
    res, _ = run_pipeline(tmp_path, [primary, backup])
    assert res.providers_used == ["secours"] and len(res.questions) == 1


def test_pipeline_all_providers_fail_and_no_provider(tmp_path):
    with pytest.raises(AnalysisError):
        run_pipeline(tmp_path, [MockProvider(fail_times=99)])
    with pytest.raises(AnalysisError):
        run_pipeline(tmp_path / "d", []) if (tmp_path / "d").mkdir() is None else None


# ---------------------------------------------------------------- budget
def test_budget_blocks_paid_but_falls_back_to_free(tmp_path):
    paid = MockProvider(paid=True, price_in=1000.0, price_out=1000.0, name="payant")  # estimation énorme
    free = MockProvider(lambda purpose, n: analysis([q()]) if purpose == "analyze" else '{"corrections": []}', name="gratuit")
    res, store = run_pipeline(tmp_path, [paid, free], budget_daily=0.5)
    assert res.providers_used == ["gratuit"] and paid.calls == []


def test_budget_exceeded_when_only_paid_provider(tmp_path):
    paid = MockProvider(paid=True, price_in=1000.0, price_out=1000.0)
    with pytest.raises(BudgetExceeded):
        run_pipeline(tmp_path, [paid], budget_daily=0.5)


def test_budget_records_usage_and_request_cap(tmp_path):
    s = settings(tmp_path, max_requests_per_day=2, verify_with_llm=True, budget_daily=100.0, budget_monthly=100.0)
    store = Store(str(tmp_path))
    b = Budget(s, store)
    p = MockProvider(lambda purpose, n: analysis([q()]) if purpose == "analyze" else '{"corrections": []}',
                     paid=True, price_in=3.0, price_out=15.0, tokens=(1_000_000, 100_000))
    res = run(analyze_session([p], IMG, s, b))
    assert len(res.questions) == 1
    # deux appels (analyse + relecture), chacun 3 + 1.5 = 4.5
    assert b.report()["spent_today"] == pytest.approx(9.0) and b.report()["calls_today"] == 2
    with pytest.raises(BudgetExceeded):  # plafond de requêtes atteint
        b.check("x", 0.0)
