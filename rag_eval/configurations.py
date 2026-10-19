import json
import os
import dspy
import openai
from dotenv import load_dotenv


def configure_dspy():
    load_dotenv()
    api_base = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
    model_name = os.getenv("MODEL_NAME", "Qwen/Qwen3-30B-A3B-Instruct-2507-FP8")

    # Dummy key: vLLM ignores it, but LiteLLM/OpenAI requires non-empty
    dummy_key = os.getenv("LLM_API_KEY", "sk-no-key-needed")

    # Set global OpenAI client config
    openai.api_base = api_base
    openai.api_key = dummy_key

    # Create DSPy LM - pass dummy_key explicitly
    model = dspy.LM(
        model=f"openai/{model_name}",  # 'openai/' prefix signals compatible API
        api_base=api_base,
        api_key=dummy_key,  # Non-empty!
        model_type="chat",
        temperature=0.1,
        max_tokens=int(os.getenv("DSPY_MAX_TOKENS", "50000")),   # env override (this project)
        **({"extra_body": json.loads(os.environ["LLM_EXTRA_BODY"])} if os.getenv("LLM_EXTRA_BODY") else {}),
        cache=True,
        #frequency_penalty=0.2
    )

    dspy.settings.configure(lm=model, temperature=0.1)  # Reduce from default ~0.7
