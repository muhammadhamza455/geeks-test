"""Application configuration loaded from environment variables and optional .env files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from repo root or backend directory without overriding explicit shell env vars
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
load_dotenv(_REPO_ROOT / ".env", override=False)
load_dotenv(_BACKEND_DIR / ".env", override=False)


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to default when unset, blank, or malformed."""
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to default when unset, blank, or malformed."""
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _resolve_sample_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    for base in (_BACKEND_DIR, _REPO_ROOT, Path.cwd()):
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved
    return (_REPO_ROOT / "sample_events.jsonl").resolve()


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.getenv("WIKIPULSE_MODE", "live").strip().lower())
    sample_file: Path = field(
        default_factory=lambda: _resolve_sample_path(
            os.getenv("WIKIPULSE_SAMPLE_FILE", "../sample_events.jsonl")
        )
    )
    replay_interval_seconds: float = field(
        default_factory=lambda: max(0.0, _env_float("WIKIPULSE_REPLAY_INTERVAL_SECONDS", 0.35))
    )
    stream_url: str = field(
        default_factory=lambda: os.getenv(
            "WIKIPULSE_STREAM_URL", "https://stream.wikimedia.org/v2/stream/recentchange"
        )
    )
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "WIKIPULSE_USER_AGENT",
            "WikiPulse/1.0 (https://github.com/wikipulse/wikipulse; dev@wikipulse.local)",
        )
    )

    # LLM settings
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "auto").strip().lower())
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "").strip())
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    openai_base_url: str = field(
        default_factory=lambda: (
            os.getenv("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
        ).rstrip("/")
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
    )
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-haiku-5-5"
    )
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
    )

    # Spending / rate-limit guardrails
    llm_max_calls_per_minute: int = field(
        default_factory=lambda: max(1, _env_int("LLM_MAX_CALLS_PER_MINUTE", 12))
    )
    pulse_refresh_min_seconds: int = field(
        default_factory=lambda: max(5, _env_int("PULSE_REFRESH_MIN_SECONDS", 25))
    )

    # Server
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8080",
            ).split(",")
            if origin.strip()
        ]
    )

    def effective_api_key(self, provider: str) -> str:
        """Return the API key for the given provider, falling back to generic LLM_API_KEY."""
        if provider == "openai":
            return self.openai_api_key or self.llm_api_key
        if provider == "anthropic":
            return self.anthropic_api_key or self.llm_api_key
        if provider == "gemini":
            return self.gemini_api_key or self.llm_api_key
        return ""

    def resolved_llm_provider(self) -> str:
        """Resolve 'auto' to the first configured LLM provider, or 'heuristic'."""
        if self.llm_provider == "heuristic":
            return "heuristic"
        if self.llm_provider == "openai":
            return "openai" if self.effective_api_key("openai") else "heuristic"
        if self.llm_provider == "anthropic":
            return "anthropic" if self.effective_api_key("anthropic") else "heuristic"
        if self.llm_provider == "gemini":
            return "gemini" if self.effective_api_key("gemini") else "heuristic"

        # auto selection: prefer explicit provider keys, then infer from generic LLM_API_KEY
        if self.openai_api_key:
            return "openai"
        if self.anthropic_api_key:
            return "anthropic"
        if self.gemini_api_key:
            return "gemini"
        if self.llm_api_key:
            if self.llm_api_key.startswith("sk-ant-"):
                return "anthropic"
            if self.llm_api_key.startswith("AIza"):
                return "gemini"
            return "openai"
        return "heuristic"

    def provider_label(self) -> str:
        provider = self.resolved_llm_provider()
        if provider == "openai":
            return f"openai:{self.openai_model}"
        if provider == "anthropic":
            return f"anthropic:{self.anthropic_model}"
        if provider == "gemini":
            return f"gemini:{self.gemini_model}"
        return "heuristic"
