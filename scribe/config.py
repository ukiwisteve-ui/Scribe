"""Settings, loaded from a .env file next to the app (or the environment)."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent

load_dotenv(APP_DIR / ".env")


def documents_dir() -> Path:
    """The user's real Documents folder.

    On Windows this is often redirected (for example to OneDrive\\Documents),
    so ask the shell rather than assuming ~/Documents.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                             (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            path_ptr = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None,
                                            ctypes.byref(path_ptr)) == 0:
                try:
                    return Path(path_ptr.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def save_setting(name: str, value: str, env_file: Path | None = None):
    """Set NAME=value in .env (keeping everything else) and in this process."""
    env_file = env_file or APP_DIR / ".env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[name] = value


@dataclass
class Settings:
    deepgram_api_key: str
    anthropic_api_key: str
    user_name: str
    language: str
    output_dir: Path
    claude_model: str
    keep_raw_audio: bool
    vocabulary: list[str] = field(default_factory=list)
    live_transcript: bool = True
    record_prompt: bool = True

    @classmethod
    def load(cls) -> "Settings":
        output_dir = os.getenv("SCRIBE_OUTPUT_DIR") or str(documents_dir() / "Scribe")
        return cls(
            deepgram_api_key=os.getenv("DEEPGRAM_API_KEY", "").strip(),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
            user_name=os.getenv("SCRIBE_USER_NAME", "").strip() or "Me",
            language=os.getenv("SCRIBE_LANGUAGE", "").strip() or "en",
            output_dir=Path(os.path.expandvars(os.path.expanduser(output_dir))),
            claude_model=os.getenv("SCRIBE_CLAUDE_MODEL", "").strip() or "claude-opus-5-5",
            keep_raw_audio=_bool("SCRIBE_KEEP_RAW_AUDIO", False),
            vocabulary=[t.strip() for t in os.getenv("SCRIBE_VOCABULARY", "").split(",")
                        if t.strip()],
            live_transcript=_bool("SCRIBE_LIVE_TRANSCRIPT", True),
            record_prompt=_bool("SCRIBE_RECORD_PROMPT", True),
        )

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        return missing
