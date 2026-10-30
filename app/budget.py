"""Garde-fous de coût : plafonds journalier / mensuel et nombre de requêtes."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .config import Settings
from .store import Store


class BudgetExceeded(Exception):
    pass


def _start_of_day(now: float) -> float:
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _start_of_month(now: float) -> float:
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


class Budget:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def check(self, provider_name: str, estimated_cost: float) -> None:
        """Lève BudgetExceeded si l'appel dépasserait un plafond."""
        now = time.time()
        if self.store.calls_since(_start_of_day(now)) >= self.settings.max_requests_per_day:
            raise BudgetExceeded("limite de requêtes IA par jour atteinte")
        if estimated_cost <= 0:
            return
        day = self.store.spent_since(_start_of_day(now))
        if day + estimated_cost > self.settings.budget_daily:
            raise BudgetExceeded(f"budget journalier atteint ({provider_name})")
        month = self.store.spent_since(_start_of_month(now))
        if month + estimated_cost > self.settings.budget_monthly:
            raise BudgetExceeded(f"budget mensuel atteint ({provider_name})")

    def record(self, provider_name: str, in_tok: int, out_tok: int, cost: float) -> None:
        self.store.record_usage(provider_name, in_tok, out_tok, cost)

    def report(self) -> dict[str, float]:
        now = time.time()
        return {
            "spent_today": self.store.spent_since(_start_of_day(now)),
            "spent_month": self.store.spent_since(_start_of_month(now)),
            "calls_today": float(self.store.calls_since(_start_of_day(now))),
        }
