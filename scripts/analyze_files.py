"""Analyse des photos locales avec le VRAI pipeline, sans Messenger ni lunettes.

Sert à juger la qualité de l'IA et le rendu des messages sur vos propres feuilles.

    python -m scripts.analyze_files page1.jpg page2.jpg [--raw]

Variables d'environnement : mêmes que le serveur (PRIMARY_PROVIDER, clés, prix...).
Avec PRIMARY_PROVIDER=mock, les réponses sont factices (test de la chaîne seulement).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from app.ai.factory import build_chain
from app.budget import Budget
from app.config import Settings
from app.http import RequestsHttp
from app.imaging import ImageError, process_image
from app.pipeline import AnalysisError, analyze_session
from app.budget import BudgetExceeded
from app.screens import format_question
from app.store import Store


async def main(paths: list[str], raw: bool) -> int:
    settings = Settings.from_env()
    chain = build_chain(settings, RequestsHttp())
    if not chain:
        print("Aucun fournisseur utilisable (clé absente, payant non autorisé ou prix manquants).", file=sys.stderr)
        return 2

    images = []
    for i, p in enumerate(paths, start=1):
        try:
            img = process_image(
                Path(p).read_bytes(),
                max_side=settings.image_max_side,
                min_side=settings.image_min_side,
                blur_threshold=settings.blur_threshold,
            )
        except (ImageError, OSError) as exc:
            print(f"Page {i} ({p}) : {exc}", file=sys.stderr)
            return 1
        flag = "FLOU (serait rejetée)" if img.is_blurry else "nette"
        print(f"Page {i}: {img.width}x{img.height}, score de netteté {img.blur_score:.1f} → {flag}")
        images.append((img.data, "image/jpeg"))

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        try:
            budget = Budget(settings, store)
            try:
                result = await analyze_session(chain, images, settings, budget)
            except (AnalysisError, BudgetExceeded) as exc:
                print(f"Échec : {exc}", file=sys.stderr)
                return 1

            spent = budget.report()
        finally:
            store.close()

    print(f"\nFournisseur(s) : {', '.join(result.providers_used)} · coût estimé de cette analyse : {spent['spent_today']:.4f}")
    if result.unreadable_pages:
        print("Pages illisibles :", result.unreadable_pages)
    print(f"{len(result.questions)} question(s)\n" + "=" * 40)
    for i, q in enumerate(result.questions, start=1):
        for m in format_question(q, i, len(result.questions), settings.screen_chars, settings.question_chars):
            print(m + "\n" + "-" * 40)
    for w in result.warnings:
        print("⚠", w)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--raw", action="store_true", help="réservé")
    ns = ap.parse_args()
    sys.exit(asyncio.run(main(ns.images, ns.raw)))
