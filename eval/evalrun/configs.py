"""Named model configurations evaluated against the dataset."""

from dataclasses import dataclass
from typing import Final

from invoice_pilot.llm.config import LLMConfig


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """One evaluated setup: a name, the provider/model parameters, the key variable."""

    name: str
    llm: LLMConfig
    key_env: str


# Parameters as confirmed by the PM (open question Q-19, 2026-10-24): Gemini uses the
# lowest thinking level of each model and the API default media resolution; OpenAI
# uses reasoning effort "none", store=false and the given image detail.
CONFIGS: Final[dict[str, EvalConfig]] = {
    "gemini-flash-lite": EvalConfig(
        "gemini-flash-lite",
        LLMConfig(provider="gemini", model="gemini-3.5-flash-lite"),
        "GEMINI_API_KEY",
    ),
    "gemini-flash": EvalConfig(
        "gemini-flash", LLMConfig(provider="gemini", model="gemini-3.8-flash"), "GEMINI_API_KEY"
    ),
    "gpt-luna-auto": EvalConfig(
        "gpt-luna-auto",
        LLMConfig(provider="openai", model="gpt-6-luna", image_detail="auto"),
        "OPENAI_API_KEY",
    ),
    "gpt-luna-low": EvalConfig(
        "gpt-luna-low",
        LLMConfig(provider="openai", model="gpt-6-luna", image_detail="low"),
        "OPENAI_API_KEY",
    ),
}

PRIMARY_CONFIG: Final = "gemini-flash-lite"


def get_config(name: str) -> EvalConfig:
    try:
        return CONFIGS[name]
    except KeyError:
        known = ", ".join(sorted(CONFIGS))
        raise ValueError(f"unknown configuration {name!r}; known: {known}") from None
