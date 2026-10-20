"""Settings, loaded from a .env file next to the app (or the environment)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent

load_dotenv(APP_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    deepgram_api_key: str
    anthropic_api_key: str
    user_name: str
    language: str
    output_dir: Path
    claude_model: str
    keep_raw_audio: bool

    @classmethod
    def load(cls) -> "Settings":
        output_dir = os.getenv("SCRIBE_OUTPUT_DIR") or str(Path.home() / "Documents" / "Scribe")
        return cls(
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY", "").strip(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            user_name=os.getenv("SCRIBE_USER_NAME", "").strip() or "Me",
            language=os.getenv("SCRIBE_LANGUAGE", "").strip() or "en",
            output_dir=Path(os.path.expandvars(os.path.expanduser(output_dir))),
            claude_model=os.getenv("SCRIBE_CLAUDE_MODEL", "").strip() or "claude-opus-5-5",
            keep_raw_audio=_bool("SCRIBE_KEEP_RAW_AUDIO", False),
        )

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing
