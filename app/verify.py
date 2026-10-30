"""Vérification déterministe des calculs annoncés par le modèle.

L'évaluateur n'accepte que des nombres, + - * / % ** et des parenthèses : jamais
d'appel de fonction, de nom ou d'attribut (pas d'exécution de code arbitraire).
"""
from __future__ import annotations

import ast
import math
import operator as op
import re

from .models import Question

_BIN = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}
_UNARY = {ast.UAdd: op.pos, ast.USub: op.neg}
_MAX_LEN = 200


def _normalize(expr: str) -> str:
    s = expr.strip()
    s = s.replace("×", "*").replace("·", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    s = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", s)  # 1 000 -> 1000
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)  # 3,5 -> 3.5
    return s


def safe_eval(expr: str) -> float:
    s = _normalize(expr)
    if not s or len(s) > _MAX_LEN:
        raise ValueError("expression vide ou trop longue")
    tree = ast.parse(s, mode="eval")

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e12):
                raise ValueError("puissance trop grande")
            result = _BIN[type(node.op)](a, b)
            if isinstance(result, complex) or not math.isfinite(result):
                raise ValueError("résultat invalide")
            return float(result)
        raise ValueError("élément non autorisé")

    return ev(tree)


def _parse_number(text: str) -> float:
    s = text.strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"(?<=\d) (?=\d{3}\b)", "", s)
    s = s.replace(",", ".")
    s = re.sub(r"[^\d.\-+eE]", "", s)  # retire l'unité éventuelle
    return float(s)


def check_calcs(q: Question) -> list[str]:
    """Recalcule chaque calcul annoncé. Retourne la liste des écarts trouvés ;
    les expressions inexploitables sont ignorées (ni validées, ni rejetées)."""
    issues: list[str] = []
    for c in q.calcs:
        try:
            computed = safe_eval(c.expression)
            claimed = _parse_number(c.result)
        except (ValueError, SyntaxError, ZeroDivisionError, RecursionError):
            continue
        if not math.isclose(computed, claimed, rel_tol=5e-3, abs_tol=0.011):
            issues.append(f"{c.expression.strip()} = {computed:g} (annoncé : {claimed:g})")
    return issues
