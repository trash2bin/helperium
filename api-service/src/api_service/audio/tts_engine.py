"""TTS (Text-to-Speech) engine with provider chain and fallback.

Architecture
------------
TTSEngine maintains an ordered list of providers. On synthesize():
  1. Try the first enabled provider.
  2. If it fails and ``fallback_enabled``, try the next.
  3. If all fail, raise ``AllProvidersFailed``.

Built-in providers:
  - ``LiteLLMTTSProvider`` — OpenAI TTS, Azure TTS via litellm
  - ``LocalTTSProvider`` — local piper-tts / coqui / OOT via subprocess
"""

from __future__ import annotations

import time
from typing import Protocol


class TTSResult:
    """Result of a successful speech synthesis."""

    def __init__(
        self,
        audio_bytes: bytes,
        duration_seconds: float = 0.0,
        provider_name: str = "",
        format: str = "mp3",
    ) -> None:
        self.audio_bytes = audio_bytes
        self.duration_seconds = duration_seconds
        self.provider_name = provider_name
        self.format = format  # "mp3" | "wav"

    def __repr__(self) -> str:
        return (
            f"TTSResult({len(self.audio_bytes)} bytes, "
            f"duration={self.duration_seconds:.2f}s, "
            f"provider={self.provider_name!r}, "
            f"format={self.format!r})"
        )


class TTSProvider(Protocol):
    """Protocol each TTS provider must satisfy."""

    name: str

    async def synthesize(self, text: str) -> TTSResult:
        """Convert text to audio bytes."""
        ...


class LiteLLMTTSProvider:
    """TTS via LiteLLM — supports OpenAI TTS, Azure TTS.

    Uses ``litellm.aspeech()`` which is the async version.
    Returns MP3 audio bytes by default.
    """

    def __init__(
        self,
        name: str,
        model: str = "tts-1",
        voice: str = "alloy",
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.voice = voice
        self.api_key = api_key
        self.api_base = api_base

    async def synthesize(self, text: str) -> TTSResult:
        import litellm

        kwargs: dict = dict(model=self.model, input=text, voice=self.voice)
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        t0 = time.monotonic()
        response = await litellm.aspeech(**kwargs)
        elapsed = time.monotonic() - t0

        audio_bytes = getattr(response, "content", b"")
        if not audio_bytes:
            audio_bytes = getattr(response, "data", b"")
        if not audio_bytes:
            raise RuntimeError("TTS returned empty audio")

        return TTSResult(
            audio_bytes=audio_bytes,
            duration_seconds=elapsed,
            provider_name=self.name,
            format="mp3",
        )


class LocalTTSProvider:
    """Local TTS via subprocess (piper-tts, coqui, OOT, etc.).

    Expects a CLI binary that:
      - Reads text from stdin
      - Outputs raw audio (WAV) to stdout
      - Accepts ``--model`` and ``--voice`` flags
    """

    def __init__(
        self,
        name: str,
        model: str = "en_US-lessac-medium",
        voice: str = "default",
        binary_path: str = "piper-tts",
    ) -> None:
        self.name = name
        self.model = model
        self.voice = voice
        self.binary_path = binary_path

    async def synthesize(self, text: str) -> TTSResult:
        import asyncio

        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            self.binary_path,
            "--model",
            self.model,
            "--voice",
            self.voice,
            "--output-raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode()), timeout=120
        )
        if proc.returncode != 0:
            msg = stderr.decode().strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"TTS subprocess failed: {msg}")

        elapsed = time.monotonic() - t0
        return TTSResult(
            audio_bytes=stdout,
            duration_seconds=elapsed,
            provider_name=self.name,
            format="wav",
        )


class AllProvidersFailed(RuntimeError):
    """All configured TTS providers failed."""

    def __init__(self, errors: list[Exception]) -> None:
        self.errors = errors
        details = "; ".join(f"{type(e).__name__}: {e}" for e in errors)
        super().__init__(f"All TTS providers failed [{details}]")


class TTSEngine:
    """TTS engine with ordered provider chain and optional fallback.

    Usage::

        engine = TTSEngine.from_config(voice_config)
        result = await engine.synthesize("Hello world")
        # result.audio_bytes contains the audio data
    """

    def __init__(
        self,
        providers: list[TTSProvider],
        fallback_enabled: bool = True,
    ) -> None:
        self.providers = providers
        self.fallback_enabled = fallback_enabled

    async def synthesize(self, text: str) -> TTSResult:
        """Synthesize speech. Tries providers in order, fallback if enabled."""
        errors: list[Exception] = []
        for provider in self.providers:
            try:
                return await provider.synthesize(text)
            except Exception as exc:
                errors.append(exc)
                if not self.fallback_enabled:
                    raise
        raise AllProvidersFailed(errors)

    @classmethod
    def from_config(cls, config) -> TTSEngine:
        """Build a TTSEngine from a ``VoiceConfig``-like object."""
        from .voice_config import build_tts_providers

        return cls(
            providers=build_tts_providers(config),
            fallback_enabled=getattr(config, "tts_fallback_enabled", True),
        )
