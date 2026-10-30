"""Test direct du vérificateur Groq avec une réponse volontairement fausse.
Depuis la racine studylens-backend : python -m scripts.test_relecture
"""
import asyncio
import json
import sys

from app.ai.factory import build_chain
from app.config import Settings
from app.http import RequestsHttp
from app.pipeline import VERIFY_SYSTEM, _extract_json


async def main() -> int:
    settings = Settings.from_env()
    providers = build_chain(settings, RequestsHttp())
    provider = next((p for p in providers if p.name == "groq"), None)
    if provider is None:
        print("ERREUR : Groq introuvable. Vérifie PRIMARY_PROVIDER=groq et GROQ_API_KEY.")
        return 2

    payload = {
        "transcription": "[Page 2] Exercice 1 : Écris une boucle for qui affiche les nombres de 1 à 10 inclus.",
        "questions": [{
            "id": 1,
            "enonce": "Écris une boucle for qui affiche les nombres de 1 à 10 inclus.",
            "reponse": "for (let i = 1; i < 10; i++) { console.log(i); }"
        }]
    }
    print("Test avec une réponse volontairement fausse (la boucle oublie le nombre 10)...")
    try:
        result = await provider.generate(
            VERIFY_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            [],
            max_tokens=250,
            purpose="verify",
        )
    except Exception as exc:
        print(f"ÉCHEC de l'appel Groq : {exc}")
        return 1

    print("\nRéponse brute de Groq :")
    print(result.text)
    try:
        data = _extract_json(result.text)
    except ValueError:
        print("\nÉCHEC : réponse JSON illisible.")
        return 1

    corrections = data.get("corrections")
    if not isinstance(corrections, list):
        print("\nÉCHEC : champ corrections absent ou invalide.")
        return 1

    correction = next(
        (item for item in corrections
         if isinstance(item, dict) and str(item.get("id")) == "1"),
        None,
    )
    if correction and str(correction.get("reponse", "")).strip():
        print("\nRÉSULTAT : le vérificateur a proposé une correction.")
        print("Correction :", correction["reponse"])
        print("Raison :", correction.get("raison", "(aucune raison fournie)"))
        return 0

    print("\nÀ VÉRIFIER : aucune correction proposée.")
    print("Le test ne permet pas de confirmer que le vérificateur détecte cette erreur.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
