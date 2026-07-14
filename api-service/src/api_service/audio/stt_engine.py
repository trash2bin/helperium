"""STT (Speech-to-Text) engine with provider chain and fallback.

Architecture
------------
STTEngine maintains an ordered list of providers. On transcribe():
  1. Try the first enabled provider.
  2. If it fails and ``fallback_enabled``, try the next.
  3. If all fail, raise ``AllProvidersFailed``.

Built-in providers:
  - ``LiteLLMSTTProvider`` — OpenAI Whisper, Azure Whisper, Nvidia Riva via litellm
  - ``LocalSTTProvider`` — local whisper.cpp / faster-whisper via subprocess
"""

from __future__ import annotations

import io
import time
from typing import Protocol


class STTResult:
    """Result of a successful transcription."""

    def __init__(
        self,
        text: str,
        duration_seconds: float = 0.0,
        provider_name: str = "",
    ) -> None:
        self.text = text
        self.duration_seconds = duration_seconds
        self.provider_name = provider_name

    def __repr__(self) -> str:
        return (
            f"STTResult(text={self.text!r:.50}, "
            f"duration={self.duration_seconds:.2f}s, "
            f"provider={self.provider_name!r})"
        )


class STTProvider(Protocol):
    """Protocol each STT provider must satisfy."""

    name: str

    async def transcribe(self, audio_bytes: bytes) -> STTResult:
        """Transcribe audio bytes to text."""
        ...


class LiteLLMSTTProvider:
    """STT via LiteLLM — supports OpenAI Whisper, Azure Whisper, Nvidia Riva.

    Uses ``litellm.atranscription()`` which is the async version.
    Accepts any audio format that Whisper supports (webm, mp3, wav, ogg, flac, etc.).
    """

    def __init__(
        self,
        name: str,
        model: str = "whisper-1",
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.api_key = api_key
        self.api_base = api_base

    async def transcribe(self, audio_bytes: bytes) -> STTResult:
        import litellm

        kwargs: dict = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        t0 = time.monotonic()
        response = await litellm.atranscription(
            model=self.model,
            file=("audio.webm", io.BytesIO(audio_bytes), "audio/webm"),
            **kwargs,
        )
        elapsed = time.monotonic() - t0
        text = getattr(response, "text", "") or ""
        return STTResult(
            text=text.strip(),
            duration_seconds=elapsed,
            provider_name=self.name,
        )


class LocalSTTProvider:
    """Local STT via subprocess (faster-whisper / whisper.cpp).

    Expects a CLI binary that:
      - Accepts ``--file <path>`` for the audio input
      - Accepts ``--model <name>`` for model selection
      - Outputs transcribed text to stdout when given ``--output-txt -``
    """

    def __init__(
        self,
        name: str,
        model: str = "base",
        binary_path: str = "whisper-cli",
    ) -> None:
        self.name = name
        self.model = model
        self.binary_path = binary_path

    async def transcribe(self, audio_bytes: bytes) -> STTResult:
        import asyncio
        import os
        import tempfile

        t0 = time.monotonic()
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary_path,
                "--model",
                self.model,
                "--file",
                tmp_path,
                "--output-txt",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                msg = stderr.decode().strip() or f"exit code {proc.returncode}"
                raise RuntimeError(f"STT subprocess failed: {msg}")
            text = stdout.decode().strip()
        finally:
            os.unlink(tmp_path)

        elapsed = time.monotonic() - t0
        return STTResult(
            text=text,
            duration_seconds=elapsed,
            provider_name=self.name,
        )


class AllProvidersFailed(RuntimeError):
    """All configured STT providers failed."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        details = "; ".join(f"{type(e).__name__}: {e}" for e in errors)
        super().__init__(f"All STT providers failed [{details}]")


class STTEngine:
    """STT engine with ordered provider chain and optional fallback.

    Usage::

        engine = STTEngine.from_config(voice_config)
        result = await engine.transcribe(audio_bytes)
        print(result.text)
    """

    def __init__(
        self,
        providers: list[STTProvider],
        fallback_enabled: bool = True,
    ) -> None:
        self.providers = providers
        self.fallback_enabled = fallback_enabled

    async def transcribe(self, audio_bytes: bytes) -> STTResult:
        """Transcribe audio. Tries providers in order, fallback if enabled."""
        errors: list[Exception] = []
        for provider in self.providers:
            try:
                return await provider.transcribe(audio_bytes)
            except Exception as exc:
                errors.append(exc)
                if not self.fallback_enabled:
                    raise
        raise AllProvidersFailed(errors)

    @classmethod
    def from_config(cls, config) -> STTEngine:
        """Build an STTEngine from a ``VoiceConfig``-like object.

        The config must have ``stt_providers`` (iterable of objects with
        ``name``, ``provider``, ``model``, ``api_key``, ``api_base``, ``enabled``)
        and ``stt_fallback_enabled``.
        """
        from .voice_config import build_stt_providers

        return cls(
            providers=build_stt_providers(config),
            fallback_enabled=getattr(config, "stt_fallback_enabled", True),
        )
