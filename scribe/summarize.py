"""Contextual summary and action items from the transcript, written by Claude."""

from __future__ import annotations

import anthropic

SYSTEM_PROMPT = """\
You turn call transcripts into notes that the person who recorded the call can act on.

The transcript comes from automatic speech recognition, so expect misheard words, \
names spelled phonetically, and occasional lines attributed to the wrong speaker. \
Use context to read through those errors. Speaker labels like "Speaker 1" are \
anonymous: if the conversation makes it clear who someone is (they are greeted by \
name, introduce themselves, or are addressed directly), use their name and say how \
you identified them. When the recorder lists who was on the call, match the labels \
to those names using what each speaker says and is called; if a match is a guess, \
say so. Speaker separation is imperfect: one label can occasionally hold two people, \
or one person can appear under two labels. Where a line plainly belongs to someone \
else (a reply attributed to the person being answered), attribute it correctly in \
the notes. Never invent facts, owners, or deadlines that the call does not \
support; when something is unclear, say so.

Write in plain, direct British English. Do not use em dashes."""

OUTPUT_FORMAT = """\
Write the notes in Markdown using exactly these sections:

# <A short, specific title for the call>

**Participants:** who was on the call (map speaker labels to names where you can).

## Summary
Three to six sentences: what the call was for, what happened, and where things landed.

## Key points
The substantive points discussed, grouped by topic, with enough detail to be useful \
to someone who missed the call.

## Decisions
Decisions that were actually made. Write "None recorded." if there were none.

## Action items
A Markdown table with columns: Owner | Action | Due | Notes. Include every commitment \
or request, including small ones. Use "Unassigned" when no owner was agreed and leave \
Due blank when no date was mentioned. Put actions for {user_name} first.

## Open questions and risks
Unresolved questions, concerns, or blockers raised.

## Suggested follow-up
A short follow-up message {user_name} could send to the other participants, \
recapping decisions and actions."""


def build_prompt(transcript: str, user_name: str, context: str, recorded_at: str,
                 duration: str, participants: str = "", recording_note: str = "") -> str:
    context_block = context.strip() or "(none given)"
    participants_block = participants.strip() or "(not given)"
    note_line = f"Recording note: {recording_note}\n" if recording_note else ""
    return f"""\
<call_details>
Recorded by: {user_name} (their microphone is labelled "{user_name}" in the transcript)
Recorded at: {recorded_at}
Duration: {duration}
Context from {user_name}: {context_block}
Who was on the call, according to {user_name}: {participants_block}
{note_line}</call_details>

<transcript>
{transcript}
</transcript>

{OUTPUT_FORMAT.format(user_name=user_name)}"""


def summarize(transcript: str, *, api_key: str, model: str, user_name: str, context: str,
              recorded_at: str, duration: str, participants: str = "",
              recording_note: str = "") -> str:
    client = anthropic.Anthropic(api_key=api_key, max_retries=4)
    prompt = build_prompt(transcript, user_name, context, recorded_at, duration, participants,
                          recording_note)
    with client.beta.messages.stream(
        model=model,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # If a safety classifier declines, let the API retry on a suitable model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined to summarise this transcript.")
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise RuntimeError(f"Claude returned no summary (stop reason: {message.stop_reason}).")
    if message.stop_reason == "max_tokens":
        text += "\n\n_(The summary was cut off because it hit the length limit.)_"
    return text
