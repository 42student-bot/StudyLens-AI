"""Découpage des réponses en messages courts, adaptés à l'écran des lunettes.

SCREEN_CHARS est un réglage, pas une spécification matérielle : la taille
réellement confortable doit être mesurée sur vos lunettes.
"""
from __future__ import annotations

from .models import Question

ORIGIN_LABEL = {
    "document": "doc",
    "general": "connaissances générales",
    "mixte": "doc + connaissances",
}


def split_text(text: str, limit: int, first_limit: int | None = None) -> list[str]:
    """Découpe un texte en morceaux <= limit (le premier <= first_limit).

    Les retours à la ligne sont conservés, les lignes vides ignorées, aucun mot
    n'est perdu ; un mot plus long que la limite est coupé net.
    """
    limit = max(limit, 20)
    first = max(first_limit if first_limit is not None else limit, 20)
    chunks: list[str] = []
    cur = ""

    def cap() -> int:
        return first if not chunks else limit

    def flush() -> None:
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for line in text.strip().splitlines():
        for i, word in enumerate(line.split()):
            while len(word) > cap():
                flush()
                n = cap()
                chunks.append(word[:n])
                word = word[n:]
            if not word:
                continue
            sep = ("\n" if i == 0 else " ") if cur else ""
            if len(cur) + len(sep) + len(word) > cap():
                flush()
                cur = word
            else:
                cur += sep + word
    flush()
    return chunks


def _trim(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _meta(q: Question) -> str:
    parts = []
    if q.source:
        parts.append(q.source)
    parts.append(ORIGIN_LABEL.get(q.origin, q.origin))
    if q.ambiguous:
        parts.append("⚠ ambigu")
    if q.confidence == "faible":
        parts.append("⚠ incertain")
    line = "— " + " · ".join(parts)
    if q.note:
        line += "\nNote : " + _trim(q.note, 120)
    return line


def format_question(
    q: Question, idx: int, total: int, limit: int, question_chars: int = 160
) -> list[str]:
    """Messages d'une question : le premier contient l'énoncé (abrégé) et le
    début de la réponse ; les suivants la suite ; la source clôt le dernier."""
    header = f"Q{idx}/{total}"
    if q.label and q.label != str(idx):
        header += f" (n°{q.label})"
    first_prefix = f"{header}\n{_trim(q.text, question_chars)}\n\nRÉPONSE\n"
    cont_prefix = f"{header} suite\n"
    meta = _meta(q)

    answer = q.answer.strip() or "Informations insuffisantes pour répondre."
    pieces = split_text(
        answer,
        limit=max(limit - len(cont_prefix), 60),
        first_limit=max(limit - len(first_prefix), 60),
    )
    messages = [(first_prefix if i == 0 else cont_prefix) + p for i, p in enumerate(pieces)]

    if len(messages[-1]) + 1 + len(meta) <= limit:
        messages[-1] += "\n" + meta
    else:
        messages.append(f"{cont_prefix}{meta}")
    return messages
