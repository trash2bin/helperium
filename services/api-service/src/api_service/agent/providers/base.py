"""Abstract base that every LLM provider transport must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CompletionRequest, CompletionResponse


class BaseLLMProvider(ABC):
    """Mandatory contract for every LLM provider and provider-shaped wrapper.

    Every transport (LiteLLM, scripted fixture) and every wrapper that can sit
    on a provider seat (fallback chain, pool worker, answer normalizer)
    inherits this base.

    Required completion protocol:

    - ``async complete(request: CompletionRequest) -> CompletionResponse`` —
      abstract here, so a provider cannot be instantiated without it.

    Required identity surface (read by the factory, the pool, the health
    endpoint and :meth:`identity`; declared by each subclass because
    ``@dataclass`` workers and delegating wrappers need different shapes):

    - ``model: str`` — model identifier sent upstream;
    - ``provider: str | None`` — provider prefix, ``None`` = infer;
    - ``api_base: str | None`` — API base override;
    - ``enable_thinking: bool`` — whether reasoning mode was requested.

    Request serialization, retry policy and wire-level quirks stay
    transport-specific — the agent loop itself only ever calls
    :meth:`complete`.
    """

    __slots__ = ()

    #: Model identifier sent upstream (e.g. ``openai/gpt-4o-mini``).
    model: str

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Return one completion for the typed request."""

    def identity(self) -> tuple[str, str, str]:
        """Credential-free ``(provider, model, api_base)`` identity.

        Used to deduplicate resolution candidates and in logs; never includes
        API keys. ``provider``/``api_base`` are part of the required identity
        surface (see the class docstring) but are read defensively here
        because dataclass workers and delegating wrappers declare them in
        different ways.
        """
        return (
            getattr(self, "provider", None) or "",
            self.model,
            (getattr(self, "api_base", None) or "").rstrip("/"),
        )
