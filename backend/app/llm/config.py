import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(override=True)

DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMConfig(BaseModel):
    """Configuration settings for OpenRouter LLM connection."""
    api_key: str = Field(..., description="OpenRouter API key")
    model: str = Field(DEFAULT_MODEL, description="Model identifier for OpenRouter")
    base_url: str = Field(DEFAULT_BASE_URL, description="Base API endpoint for OpenRouter")
    temperature: float = Field(0.2, ge=0.0, le=2.0, description="Sampling temperature")
    timeout_seconds: float = Field(45.0, gt=0.0, description="HTTP request timeout in seconds")


def get_llm_config(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    timeout_seconds: Optional[float] = None,
) -> LLMConfig:
    """
    Loads and validates LLM configuration from environment variables or explicit parameters.
    Raises ValueError if OPENROUTER_API_KEY is not configured.
    """
    load_dotenv(override=True)
    if api_key is not None:
        key = api_key.strip()
    else:
        key = (os.getenv("OPENROUTER_API_KEY") or "").strip()

    if not key:
        raise ValueError("OPENROUTER_API_KEY is not configured in environment or provided explicitly.")

    chosen_model = (model or os.getenv("OPENROUTER_MODEL") or DEFAULT_MODEL).strip()

    return LLMConfig(
        api_key=key,
        model=chosen_model,
        base_url=DEFAULT_BASE_URL,
        temperature=temperature if temperature is not None else 0.2,
        timeout_seconds=timeout_seconds if timeout_seconds is not None else 45.0,
    )
