from langchain_openai import ChatOpenAI  # Use ChatOpenAI for vLLM / any OpenAI-compatible endpoint
import json
import os
from dotenv import load_dotenv

# Load the environment
load_dotenv()


def _endpoint(port: int = 8000):
    """LLM_BASE_URL / LLM_API_KEY if set; else OPENROUTER_API_KEY -> OpenRouter; else a local vLLM server (original behaviour)."""
    key = os.getenv("OPENROUTER_API_KEY")
    base = os.getenv("LLM_BASE_URL") or ("https://openrouter.ai/api/v1" if key else f"http://localhost:{port}/v1")
    return base, os.getenv("LLM_API_KEY") or key or "not-needed"


def connect_to_vllm(model_name: str = None, temperature: float = 0.3, max_tokens: int = 4096, port: int = 8000) -> ChatOpenAI:
    """Initializes and returns the ChatOpenAI client for the vLLM server (or OpenRouter, see _endpoint)."""
    print("🔌 Connecting to the LLM endpoint...")

    model_name = os.getenv("MODEL_NAME", "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8")
    base_url, api_key = _endpoint(port)

    llm = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", max_tokens)),
        timeout=float(os.getenv("LLM_TIMEOUT", "120")),
        max_retries=1,
        # optional provider parameters as JSON, e.g. {"reasoning": {"enabled": false}} for reasoning models on OpenRouter
        extra_body=json.loads(os.getenv("LLM_EXTRA_BODY") or "null"),
    )
    print("✅ Successfully connected.")
    return llm


# name used by agents/cq_generator.py in the original repository
get_vllm_llm = connect_to_vllm

if __name__ == "__main__":
    connect_to_vllm()
