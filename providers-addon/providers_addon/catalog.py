"""
Provider catalog for the omni-harness BYOK addon.

Each provider entry carries its OpenAI-compatible base URL, the env-var name
the agent will also check for a key, and a static model list that is used as
the fallback when the live /models endpoint is unreachable.

The live fetch always wins: static data here is only a safety net and a quick
reference so the UI can show limits even before the first successful API call.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Rate-limit record
# ---------------------------------------------------------------------------
def _lim(rpm: int | None = None, tpm: int | None = None,
         ctx: int | None = None, note: str = "") -> dict:
    return {"rpm": rpm, "tpm": tpm, "ctx": ctx, "note": note}


# ---------------------------------------------------------------------------
# NVIDIA NIM  (api.nvidia.com/v1)
# Free tier: 40 RPM, 1 K TPM across the account
# Paid tier: varies per model and subscription
# ---------------------------------------------------------------------------
NVIDIA_MODELS: list[dict] = [
    # name, description, limits
    {"id": "meta/llama-3.3-70b-instruct",
     "description": "Llama 3.3 70B — instruction-tuned, strong code and reasoning",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "meta/llama-3.1-405b-instruct",
     "description": "Llama 3.1 405B — largest open model",
     **_lim(rpm=10, tpm=1000, ctx=131072)},
    {"id": "nvidia/llama-3.1-nemotron-70b-instruct",
     "description": "Nemotron 70B — NVIDIA RLHF-tuned Llama for alignment",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "deepseek-ai/deepseek-r1",
     "description": "DeepSeek-R1 671B — chain-of-thought reasoning powerhouse",
     **_lim(rpm=10, tpm=1000, ctx=65536, note="slow; best for hard problems")},
    {"id": "deepseek-ai/deepseek-r1-distill-llama-70b",
     "description": "DeepSeek-R1 distilled into Llama 70B — fast reasoner",
     **_lim(rpm=40, tpm=1000, ctx=65536)},
    {"id": "qwen/qwen2.5-72b-instruct",
     "description": "Qwen 2.5 72B — multilingual, strong on code",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "qwen/qwen2.5-coder-32b-instruct",
     "description": "Qwen 2.5 Coder 32B — specialist code model",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "mistralai/mistral-large-2-instruct",
     "description": "Mistral Large 2 — 123B, strong tool use",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "google/gemma-3-27b-it",
     "description": "Gemma 3 27B — Google open model, good at instruction following",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "microsoft/phi-4",
     "description": "Phi-4 14B — Microsoft small-but-capable model",
     **_lim(rpm=40, tpm=1000, ctx=16384)},
    {"id": "meta/llama-3.1-70b-instruct",
     "description": "Llama 3.1 70B — solid general-purpose model",
     **_lim(rpm=40, tpm=1000, ctx=131072)},
    {"id": "meta/llama-3.1-8b-instruct",
     "description": "Llama 3.1 8B — fastest model on NVIDIA NIM",
     **_lim(rpm=60, tpm=1000, ctx=131072)},
    {"id": "nvidia/llama-3.1-nemotron-nano-8b-v1",
     "description": "Nemotron Nano 8B — efficient NVIDIA-tuned model",
     **_lim(rpm=60, tpm=1000, ctx=131072)},
]

# ---------------------------------------------------------------------------
# MiniMax  (api.minimax.io/v1)
# ---------------------------------------------------------------------------
MINIMAX_MODELS: list[dict] = [
    {"id": "MiniMax-Text-01",
     "description": "MiniMax Text-01 — 456B MoE, 1M context window",
     **_lim(rpm=100, tpm=128000, ctx=1000000)},
    {"id": "MiniMax-M1",
     "description": "MiniMax M1 — reasoning model, 1M context",
     **_lim(rpm=10, tpm=128000, ctx=1000000, note="reasoning model; slower")},
    {"id": "abab6.5s-chat",
     "description": "ABAB 6.5S — fast and cheap, 245K context",
     **_lim(rpm=60, tpm=256000, ctx=245760)},
    {"id": "abab6.5g-chat",
     "description": "ABAB 6.5G — balanced speed/quality",
     **_lim(rpm=60, tpm=256000, ctx=8192)},
    {"id": "abab5.5s-chat",
     "description": "ABAB 5.5S — lightweight, low-latency",
     **_lim(rpm=60, tpm=256000, ctx=16384)},
]

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, dict] = {
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "env_key": "NVIDIA_API_KEY",
        "get_key_url": "https://build.nvidia.com/explore/discover",
        "models": NVIDIA_MODELS,
        # live /models returns objects; we match by id prefix
        "id_field": "id",
        "note": "Free tier: 40 RPM, 1 K TPM shared across all models",
    },
    "minimax": {
        "label": "MiniMax.io",
        "base_url": "https://api.minimax.io/v1",
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
        "env_key": "MINIMAX_API_KEY",
        "get_key_url": "https://platform.minimaxi.com/user-center/basic-information/interface-key",
        "models": MINIMAX_MODELS,
        "id_field": "id",
        "note": "Pay-as-you-go; MiniMax-Text-01 has 1M context",
    },
}


def provider_names() -> list[str]:
    return list(PROVIDERS)


def get_provider(name: str) -> dict | None:
    return PROVIDERS.get(name.lower())


def static_models(name: str) -> list[dict]:
    p = get_provider(name)
    return p["models"] if p else []
