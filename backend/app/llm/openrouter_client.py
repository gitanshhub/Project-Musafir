import time
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from app.llm.config import LLMConfig, get_llm_config


# --- Exceptions ---

class LLMError(Exception):
    """Base exception for all LLM client errors."""
    pass


class LLMConfigError(LLMError):
    """Raised when LLM configuration or API key is missing or invalid."""
    pass


class LLMAuthError(LLMError):
    """Raised when authentication with OpenRouter fails (e.g. 401)."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limits are exceeded (e.g. 429)."""
    pass


class LLMProviderError(LLMError):
    """Raised when OpenRouter or downstream provider returns an error (e.g. 5xx)."""
    pass


class LLMTimeoutError(LLMError):
    """Raised when an LLM HTTP request times out."""
    pass


class LLMResponseError(LLMError):
    """Raised when the LLM response is malformed or missing expected fields."""
    pass


# --- Response Representation ---

class LLMResponse(BaseModel):
    """Normalized response representation from the LLM provider."""
    content: Optional[str] = Field(None, description="Generated text response from the model")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="List of tool calls requested by the model")
    model: str = Field(..., description="Actual model that generated the response")
    input_tokens: Optional[int] = Field(None, description="Number of prompt tokens consumed")
    output_tokens: Optional[int] = Field(None, description="Number of completion tokens generated")
    total_tokens: Optional[int] = Field(None, description="Total tokens consumed")
    raw_response: Optional[Dict[str, Any]] = Field(None, description="Raw provider response payload")


# --- OpenRouter Client ---

class OpenRouterClient:
    """
    Lightweight HTTP client communicating with OpenRouter's OpenAI-compatible API.
    Decoupled from vendor-specific SDKs to allow seamless model/provider switching.
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        try:
            self.config = config or get_llm_config()
        except ValueError as exc:
            raise LLMConfigError(str(exc)) from exc

    def send_message(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """
        Sends a chat completion request to OpenRouter.
        Normalizes the response into LLMResponse.
        """
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        target_model = model or self.config.model
        temp = temperature if temperature is not None else self.config.temperature

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/project-musafir",
            "X-Title": "Project Musafir",
        }

        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
        }
        if tools is not None:
            payload["tools"] = tools

        max_retries = 3
        backoff_seconds = 2.0
        response = None

        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds) as client:
                    response = client.post(endpoint, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError("LLM request timed out while communicating with OpenRouter.") from exc
            except httpx.RequestError as exc:
                raise LLMProviderError(f"Failed to connect to OpenRouter: {type(exc).__name__}") from exc

            if response.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff_seconds * (attempt + 1))
                    continue
                else:
                    raise LLMRateLimitError("OpenRouter rate limit exceeded.")

            # If not 429, exit retry loop
            break

        # Handle HTTP status codes
        if response.status_code == 401:
            raise LLMAuthError("OpenRouter authentication failed: Invalid or expired API key.")
        elif response.status_code == 429:
            raise LLMRateLimitError("OpenRouter rate limit exceeded.")
        elif response.status_code >= 500:
            raise LLMProviderError(f"OpenRouter server error (HTTP {response.status_code}).")
        elif response.status_code != 200:
            error_msg = response.text
            try:
                err_json = response.json()
                if "error" in err_json:
                    error_msg = str(err_json["error"])
            except Exception:
                pass
            raise LLMProviderError(f"OpenRouter API error (HTTP {response.status_code}): {error_msg}")

        # Parse JSON
        try:
            data = response.json()
        except Exception as exc:
            raise LLMResponseError("Invalid LLM response: Unable to parse JSON response.") from exc

        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise LLMResponseError("Invalid LLM response: 'choices' array is missing or empty.")

        message_obj = choices[0].get("message")
        if not message_obj or not isinstance(message_obj, dict):
            raise LLMResponseError("Invalid LLM response: 'message' object is missing.")

        tool_calls = message_obj.get("tool_calls")
        content = message_obj.get("content")
        if content is None and not tool_calls:
            # Check for reasoning / thought fields from thinking models (e.g. Gemini, DeepSeek, Gemma)
            reasoning = message_obj.get("reasoning") or message_obj.get("thought")
            if reasoning and str(reasoning).strip():
                content = str(reasoning).strip()
            else:
                content = ""

        usage = data.get("usage") or {}
        returned_model = data.get("model") or target_model

        return LLMResponse(
            content=content.strip() if content else "",
            tool_calls=tool_calls,
            model=returned_model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            raw_response=data,
        )
