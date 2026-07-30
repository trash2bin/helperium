"""Voice config endpoints."""

from __future__ import annotations
from fastapi import APIRouter
from api_service.audio.voice_config import load_voice_config, save_voice_config
from api_service.http_models import VoiceConfig
from api_service.utils import preserve_fields

router = APIRouter()


@router.get("/api/voice-config")
async def get_voice_config():
    config = load_voice_config()
    return config.model_dump(mode="json")


@router.put("/api/voice-config")
async def update_voice_config(body: dict) -> dict:
    current = load_voice_config()
    preserve_fields(
        body.get("stt_providers", []), current.stt_providers, ["api_key", "api_base"]
    )
    config = VoiceConfig(**body)
    save_voice_config(config)
    return config.model_dump(mode="json")
