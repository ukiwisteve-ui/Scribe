"""Past calls: listing and searching session folders, and asking Claude about them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import notes, summarize
from .config import Settings
from .pipeline import SESSION_FILE

# How much text to send when asking about past calls (about 100k tokens).
# Newer calls go first; older ones lose their transcript, then drop out.
ASK_BUDGET_CHARS = 400_000

ASK_SYSTEM_PROMPT = """\
You answer questions about the user's past calls, using only the call notes and \
transcripts provided. The transcripts come from automatic speech recognition, so \
expect misheard words and names. Say which call each point comes from (its date \
and title). If the calls provided do not answer the question, say so plainly \
rather than guessing. Keep the answer short and use Markdown lists where they help.

Write in plain, direct British English. Do not use em dashes."""


@dataclass
class PastCall:
    folder: Path
    started: str
    title: str
    duration_seconds: float = 0.0
    has_summary: bool = False

    def read(self, name: str) -> str:
        path = self.folder / name
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @property
    def summary(self) -> str:
        return self.read("summary.md")

    @property
    def transcript(self) -> str:
        return self.read("transcript.md")


def list_calls(output_dir: Path) -> list[PastCall]:
    """Every session folder under `output_dir`, newest first."""
    calls = []
    try:
        folders = [p for p in output_dir.iterdir() if (p / SESSION_FILE).is_file()]
    except OSError:
        return []
    for folder in folders:
        try:
            meta = json.loads((folder / SESSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summary_path = folder / "summary.md"
        title = ""
        if summary_path.exists():
            try:
                title = notes.title(summary_path.read_text(encoding="utf-8"))
            except OSError:
                pass
        context = (meta.get("context") or "").strip()
        title = title or (context.splitlines()[0] if context else "") or "Untitled call"
        calls.append(PastCall(folder=folder, started=meta.get("started") or folder.name,
                              title=title, duration_seconds=meta.get("duration_seconds") or 0,
                              has_summary=summary_path.exists()))
    calls.sort(key=lambda c: (c.started, c.folder.name), reverse=True)
    return calls


def search(calls: list[PastCall], query: str) -> list[PastCall]:
    """Calls whose title, notes or transcript contain every word of `query`."""
    words = query.casefold().split()
    if not words:
        return list(calls)
    found = []
    for call in calls:
        haystack = f"{call.title}\n{call.started}\n{call.summary}\n{call.transcript}".casefold()
        if all(word in haystack for word in words):
            found.append(call)
    return found


def build_question_prompt(question: str, calls: list[PastCall], user_name: str = "",
                          budget: int = ASK_BUDGET_CHARS) -> tuple[str, int]:
    """The prompt for a question about `calls`. Returns (prompt, number of calls included)."""
    parts, used = [], 0
    for call in calls:
        summary, transcript = call.summary.strip(), call.transcript.strip()
        block = (f'<call date="{call.started}" title="{call.title}">\n'
                 f"<notes>\n{summary or '(no notes)'}\n</notes>\n")
        with_transcript = block + f"<transcript>\n{transcript}\n</transcript>\n</call>"
        if transcript and used + len(with_transcript) <= budget:
            block = with_transcript
        else:
            block += "</call>"
            if used + len(block) > budget:
                break
        parts.append(block)
        used += len(block)
    calls_text = "\n\n".join(parts) if parts else "(no calls)"
    asker = (f"The question is from {user_name}, who recorded these calls (their microphone "
             f'is labelled "{user_name}" in the transcripts).\n') if user_name else ""
    prompt = (f"<calls>\n{calls_text}\n</calls>\n\n{asker}"
              f"Question about these calls: {question.strip()}")
    return prompt, len(parts)


def ask(question: str, calls: list[PastCall], settings: Settings,
        on_text: Callable[[str], None] | None = None) -> str:
    prompt, _ = build_question_prompt(question, calls, getattr(settings, "user_name", ""))
    return summarize.ask_claude(prompt, system=ASK_SYSTEM_PROMPT,
                                api_key=settings.anthropic_api_key,
                                model=settings.claude_model, on_text=on_text, what="answer")
