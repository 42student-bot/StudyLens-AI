"""Rendu PNG léger des réponses qui demandent un schéma ou un tableau."""
from __future__ import annotations

import io
import re
import textwrap
from PIL import Image, ImageDraw, ImageFont

TRIGGERS = (
    "schema", "schéma", "mld", "modele logique", "modèle logique",
    "uml", "diagramme", "tableau", "organigramme", "adressage ip",
    "modele relationnel", "modèle relationnel", "entite-association",
    "entité-association",
)


def should_render(question: str, answer: str) -> bool:
    """Envoie une image uniquement si la question demande un schema ou diagramme."""
    text = question.lower()
    normalized = text.translate(str.maketrans({
        "?": "e", "?": "e", "?": "e", "?": "e",
        "?": "a", "?": "a",
        "?": "i", "?": "i",
        "?": "o",
        "?": "u", "?": "u",
        "?": "c",
    }))

    visual_requests = (
        "schema", "diagramme", "uml", "mld",
        "modele logique de donnees",
        "modele relationnel",
        "vue relationnelle",
        "schema relationnel",
        "entite-association",
        "diagramme de classes",
        "diagramme de sequence",
        "diagramme de cas d'utilisation",
        "diagramme de cas d utilisation",
        "represente les relations",
        "dessine les relations",
        "dessine les entites",
        "representation graphique",
    )

    return any(term in normalized for term in visual_requests)


def render_answer(question: str, answer: str) -> bytes:
    """Convertit une réponse structurée en carte PNG lisible, sans appel IA."""
    title = "StudyLens · Résultat"
    q = " ".join(question.split())
    body = answer.strip() or "Aucune réponse disponible."
    # Limiter la taille pour rester raisonnable sur une instance gratuite.
    body = body[:6500]
    width, margin = 1200, 64
    font_title = ImageFont.load_default(size=38)
    font_question = ImageFont.load_default(size=28)
    font_body = ImageFont.load_default(size=25)
    line_width = 65
    lines: list[str] = []
    for raw in body.splitlines():
        if not raw.strip():
            lines.append("")
        else:
            # Conserver la structure des tableaux et des relations écrites en texte.
            lines.extend(textwrap.wrap(raw, width=line_width, replace_whitespace=False,
                                       drop_whitespace=True, subsequent_indent="  ") or [""])
    line_h = 36
    question_lines = textwrap.wrap(q, width=62) or [q]
    height = 190 + len(question_lines) * 38 + len(lines) * line_h + 80
    height = max(420, min(height, 7000))
    img = Image.new("RGB", (width, height), "#f4f7fb")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((24, 24, width-24, height-24), radius=28, fill="white", outline="#d8e1ee", width=3)
    draw.rounded_rectangle((24, 24, width-24, 100), radius=28, fill="#183153")
    draw.text((margin, 45), title, fill="white", font=font_title)
    y = 125
    draw.text((margin, y), "CONSIGNE", fill="#245b8f", font=font_question)
    y += 42
    for line in question_lines:
        draw.text((margin, y), line, fill="#172334", font=font_question)
        y += 36
    y += 18
    draw.line((margin, y, width-margin, y), fill="#d8e1ee", width=2)
    y += 24
    draw.text((margin, y), "RÉSULTAT", fill="#245b8f", font=font_question)
    y += 44
    for line in lines:
        if y > height - 50:
            break
        draw.text((margin, y), line, fill="#172334", font=font_body)
        y += line_h
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
