from __future__ import annotations

from dataclasses import dataclass

Image = tuple[bytes, str]  # (octets, type MIME)


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider:
    """Interface commune des fournisseurs d'IA multimodale."""

    name: str = "base"
    free_tier: bool = False
    price_in: float | None = None  # par million de tokens
    price_out: float | None = None

    async def generate(
        self,
        system: str,
        user_text: str,
        images: list[Image],
        *,
        max_tokens: int = 4096,
        purpose: str = "analyze",
    ) -> LLMResult:
        raise NotImplementedError

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        if self.free_tier:
            return 0.0
        pin = self.price_in or 0.0
        pout = self.price_out or 0.0
        return (input_tokens * pin + output_tokens * pout) / 1_000_000
