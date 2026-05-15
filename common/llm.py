"""LLM factory. Returns an OpenAI-compatible chat model."""

import os

from langchain_openai import ChatOpenAI


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY or OPENROUTER_API_KEY is not set - copy .env.example to .env"
        )

    if os.environ.get("OPENAI_API_KEY"):
        default_model = "gpt-4o-mini"
        default_base_url = "https://api.openai.com/v1"
    else:
        default_model = "openai/gpt-4o-mini"
        default_base_url = "https://openrouter.ai/api/v1"

    return ChatOpenAI(
        model=os.environ.get("LLM_MODEL", default_model),
        base_url=os.environ.get("LLM_BASE_URL", default_base_url),
        api_key=api_key,
        temperature=temperature,
        timeout=90,
        max_retries=5,
    )
