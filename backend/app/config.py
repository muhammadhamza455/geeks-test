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
        default_factory=lambda: float(os.getenv("WIKIPULSE_REPLAY_INTERVAL_SECONDS", "0.35"))
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
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    openai_base_url: str = field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    )
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip())
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", "").strip())
    anthropic_model: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
    )
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip())
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip())

    # Spending / rate-limit guardrails
    llm_max_calls_per_minute: int = field(
        default_factory=lambda: max(1, int(os.getenv("LLM_MAX_CALLS_PER_MINUTE", "12")))
    )
    pulse_refresh_min_seconds: int = field(
        default_factory=lambda: max(5, int(os.getenv("PULSE_REFRESH_MIN_SECONDS", "25")))
    )

    # Server
    cors_origins: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8080",
            ).split(",")
            if origin.strip()
        ]
    )

    def resolved_llm_provider(self) -> str:
        """Resolve 'auto' to the first configured LLM provider, or 'heuristic'."""
        if self.llm_provider == "heuristic":
            return "heuristic"
        if self.llm_provider == "openai":
            return "openai" if self.openai_api_key else "heuristic"
        if self.llm_provider == "anthropic":
            return "anthropic" if self.anthropic_api_key else "heuristic"
        if self.llm_provider == "gemini":
            return "gemini" if self.gemini_api_key else "heuristic"

        # auto selection
        if self.openai_api_key:
            return "openai"
        if self.anthropic_api_key:
            return "anthropic"
        if self.gemini_api_key:
            return "gemini"
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
