"""Tests d'intégration de la relecture StudyLens, sans appel API."""

import asyncio
import json
from dataclasses import replace

import app.pipeline as pipeline
from app.ai.base import LLMResult, ProviderError
from app.config import Settings

ANALYSIS = {
    "pages": [
        {
            "page": 1,
            "lisible": True,
            "type": "texte",
            "transcription": (
                "Exercice : afficher les nombres de 1 à 10 inclus."
            ),
        }
    ],
    "questions": [
        {
            "numero": "1",
            "enonce": "Afficher les nombres de 1 à 10 inclus.",
            "reponse": (
                "for (let i = 1; i < 10; i++) "
                "{ console.log(i); }"
            ),
            "source": "Page 1",
            "origine": "document",
            "confiance": "moyenne",
            "ambigue": False,
            "note": "",
            "calculs": [],
        },
        {
            "numero": "2",
            "enonce": "Afficher le texte Bonjour.",
            "reponse": 'console.log("Bonjour");',
            "source": "Page 1",
            "origine": "document",
            "confiance": "haute",
            "ambigue": False,
            "note": "",
            "calculs": [],
        },
    ],
}


class FakeBudget:
    """Budget simulé : aucun quota réel n'est consommé."""

    def check(self, provider_name, estimated_cost):
        pass

    def record(self, provider_name, input_tokens, output_tokens, cost):
        pass


class FakeProvider:
    """Fournisseur IA simulé : aucun appel à Groq."""

    name = "fake"

    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    def cost(self, input_tokens, output_tokens):
        return 0.0

    async def generate(
        self,
        system,
        user_text,
        images,
        *,
        max_tokens=4096,
        purpose="analyze",
    ):
        self.calls.append(purpose)

        # Première étape : analyse du document.
        if purpose == "analyze":
            return LLMResult(
                json.dumps(ANALYSIS, ensure_ascii=False),
                100,
                100,
            )

        # Deuxième étape : relecture.
        if self.mode == "quota":
            raise ProviderError(
                "Erreur simulée : quota indisponible",
                retryable=True,
            )

        if self.mode == "correct":
            return LLMResult(
                json.dumps(
                    {
                        "corrections": [
                            {
                                "id": 1,
                                "reponse": (
                                    "for (let i = 1; i <= 10; i++) "
                                    "{ console.log(i); }"
                                ),
                                "confiance": "haute",
                                "raison": (
                                    "La condition doit inclure le nombre 10."
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                50,
                30,
            )

        return LLMResult(
            '{"corrections": []}',
            50,
            10,
        )


async def run_test(
    name,
    mode,
    expected_answer,
    expect_warning=False,
):
    settings = Settings.from_env()
    settings = replace(settings, verify_with_llm=True)

    provider = FakeProvider(mode)

    # Supprime uniquement l'attente de 35 secondes pendant le test.
    original_sleep = pipeline.asyncio.sleep

    async def no_wait(seconds):
        return None

    pipeline.asyncio.sleep = no_wait

    try:
        result = await pipeline.analyze_session(
            providers=[provider],
            images=[(b"image de test", "image/jpeg")],
            settings=settings,
            budget=FakeBudget(),
        )
    finally:
        pipeline.asyncio.sleep = original_sleep

    assert len(result.questions) == 2, (
        f"{name} : deux questions étaient attendues."
    )

    # Vérifie la réponse de la première question.
    assert result.questions[0].answer == expected_answer, (
        f"{name} : réponse inattendue : "
        f"{result.questions[0].answer}"
    )

    # Vérifie que la réponse correcte n'a pas été modifiée.
    correct_answer = 'console.log("Bonjour");'

    assert result.questions[1].answer == correct_answer, (
        f"{name} : la réponse correcte a été modifiée."
    )

    # Vérifie l'avertissement en cas d'échec de la relecture.
    warning_present = (
        "Relecture IA indisponible" in result.warnings
    )

    assert warning_present == expect_warning, (
        f"{name} : avertissement inattendu : {result.warnings}"
    )

    # Vérifie que les deux étapes ont été exécutées.
    assert provider.calls == ["analyze", "verify"], (
        f"{name} : étapes inattendues : {provider.calls}"
    )

    print(f"OK : {name}")
    print(f"  Réponse Q1 : {result.questions[0].answer}")
    print(f"  Réponse Q2 conservée : {result.questions[1].answer}")

    if warning_present:
        print("  L'analyse initiale a été conservée malgré l'échec.")


async def main():
    mauvaise_reponse = (
        "for (let i = 1; i < 10; i++) "
        "{ console.log(i); }"
    )

    bonne_reponse = (
        "for (let i = 1; i <= 10; i++) "
        "{ console.log(i); }"
    )

    print(
        "TESTS STUDYLENS : relecture simulée, "
        "aucun appel API.\n"
    )

    # Test 1 : le vérificateur corrige une réponse fausse.
    await run_test(
        "correction d'une réponse fausse",
        "correct",
        bonne_reponse,
    )

    # Test 2 : aucune correction n'est proposée.
    # La réponse correcte à la question 2 doit rester intacte.
    await run_test(
        "conservation des réponses sans correction",
        "no_correction",
        mauvaise_reponse,
    )

    # Test 3 : la relecture échoue, mais l'analyse reste disponible.
    await run_test(
        "échec simulé de la relecture",
        "quota",
        mauvaise_reponse,
        expect_warning=True,
    )

    print("\nSUCCÈS : les trois tests sont validés.")


if __name__ == "__main__":
    asyncio.run(main())