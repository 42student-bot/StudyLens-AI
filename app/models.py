from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# États d'une session
COLLECTING = "collecting"
ANALYZING = "analyzing"
DONE = "done"


@dataclass
class Page:
    id: int
    session_id: int
    idx: int  # numéro de page (1..n), tel qu'affiché à l'utilisateur
    path: str
    width: int
    height: int
    blur_score: float


@dataclass
class Session:
    id: int
    psid: str
    state: str
    created_at: float
    updated_at: float


@dataclass
class Calc:
    expression: str
    result: str


@dataclass
class Question:
    label: str  # numéro lu sur le document, ex. "2.a"
    text: str
    answer: str
    source: str = ""  # ex. "Page 2"
    origin: str = "document"  # document | general | mixte
    confidence: str = "moyenne"  # haute | moyenne | faible
    ambiguous: bool = False
    note: str = ""
    calcs: list[Calc] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Question":
        calcs = [Calc(**c) for c in d.get("calcs", [])]
        return Question(
            label=d.get("label", ""),
            text=d.get("text", ""),
            answer=d.get("answer", ""),
            source=d.get("source", ""),
            origin=d.get("origin", "document"),
            confidence=d.get("confidence", "moyenne"),
            ambiguous=bool(d.get("ambiguous", False)),
            note=d.get("note", ""),
            calcs=calcs,
        )
