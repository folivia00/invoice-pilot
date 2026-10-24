"""Provider selection and model parameters (filled from the settings module later)."""

from dataclasses import dataclass
from typing import Final, Literal

ProviderName = Literal["gemini", "openai"]

#: Default cap for the generated JSON (invoice answers are a few hundred tokens;
#: Gemini counts thinking tokens against this limit as well).
DEFAULT_MAX_OUTPUT_TOKENS: Final = 8192


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Everything the provider factory needs besides the API key and the HTTP client."""

    provider: ProviderName
    model: str
    #: Gemini ``thinkingConfig.thinkingLevel`` (minimal|low|medium|high).
    #: None = the lowest level the model accepts (see ``providers.gemini``).
    thinking_level: str | None = None
    #: Gemini ``generationConfig.mediaResolution`` (low|medium|high) for PDF/image input.
    #: None = not sent, the API default applies (Gemini 3/4: 560 tokens per PDF page
    #: plus native text, 1120 tokens per image), which Google recommends as the start.
    media_resolution: str | None = None
    #: OpenAI ``input_image.detail`` (auto|low|high|original).
    image_detail: str = "auto"
    #: OpenAI ``reasoning.effort``; None = model default. Extraction needs no deliberation.
    reasoning_effort: str | None = "none"
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
