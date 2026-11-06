from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_path: str = "data/receptionist.db"
    business_config: str = "business.json"
    cors_origins: str = "http://localhost:5173"

    stt_provider: Literal["mock", "openai"] = "mock"
    llm_provider: Literal["mock", "openai"] = "mock"
    tts_provider: Literal["mock", "edge"] = "mock"

    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    stt_base_url: str = ""
    stt_api_key: str = ""
    stt_model: str = "whisper-large-v3-turbo"
    tts_voice: str = "en-US-JennyNeural"

    require_confirmation: bool = True
    sample_rate: int = 16000
    max_utterance_seconds: int = 30
    max_tool_steps: int = 6
    provider_timeout_seconds: float = 30
    history_messages: int = 12

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_providers(self) -> None:
        if self.llm_provider == "openai" and not self.llm_api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER=openai")
        if self.stt_provider == "openai" and not (self.stt_api_key or self.llm_api_key):
            raise RuntimeError("STT_API_KEY (or LLM_API_KEY) is required when STT_PROVIDER=openai")


@lru_cache
def get_settings() -> Settings:
    return Settings()
