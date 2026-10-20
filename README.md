# Scribe

Scribe records your calls straight from your Windows PC's audio, so it works with
Teams, Zoom, Google Meet, Slack, WhatsApp, a phone app or anything else that plays
sound. Nobody has to invite a bot. When you press **Stop**, it transcribes the call
with speaker labels and writes up a summary with decisions and action items.

## How it works

1. **Recording.** Scribe captures two things at once: everything your PC plays
   (Windows "loopback" audio, which is the other people on the call) and your
   microphone (you). They are kept on separate channels so the transcript knows
   which lines are yours.
2. **Transcription.** The recording is sent to [Deepgram](https://deepgram.com)
   Nova-3, which transcribes it and separates the different voices
   ("Speaker 1", "Speaker 2", ...).
3. **Notes.** The transcript goes to Claude (Opus 5.5 by default), which writes a
   title, a summary, key points, decisions, an action items table with owners and
   dates, open questions and a ready-to-send follow-up message. It works out
   people's names from the conversation where it can.

Everything is saved in a folder per call under `Documents\Scribe`:

| File | What it is |
| --- | --- |
| `summary.md` | The notes and action items |
| `transcript.md` | The full transcript with timestamps and speakers |
| `recording.flac` | The audio (you on the left channel, everyone else on the right) |
| `session.json`, `transcript.json`, `deepgram_response.json` | Data used to reprocess the call |

## Setup (once)

You need:

* Windows 10 or 11
* **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/windows/).
  In the installer, tick **"Add python.exe to PATH"**.
* A **Deepgram API key**: sign up at [console.deepgram.com](https://console.deepgram.com)
  (new accounts get free credit), then create a key under *API Keys*.
* An **Anthropic API key**: create one at
  [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
  and add some credit under *Billing*.

Then:

1. Download this repository (green **Code** button, then **Download ZIP**) and
   unzip it somewhere permanent, for example `C:\Users\<you>\Scribe`.
   Or, with Git: `git clone https://github.com/ukiwisteve-ui/Scribe.git`
2. Double-click **`Setup.bat`** in that folder. (If Windows shows a blue
   "Windows protected your PC" box, click **More info**, then **Run anyway**.)

   This creates a private Python environment in `.venv`, installs what Scribe
   needs, adds a **Scribe** shortcut to your desktop, and opens `.env` in Notepad.
   It takes a few minutes the first time.
3. If you prefer PowerShell, running
   `powershell -ExecutionPolicy Bypass -File setup.ps1` in the folder does the same.
4. In `.env`, paste your two keys after `DEEPGRAM_API_KEY=` and
   `ANTHROPIC_API_KEY=`, set `SCRIBE_USER_NAME` to your name, and save.

## Using it

1. Open **Scribe** from the desktop shortcut (or double-click `Scribe.bat`).
2. Check the two devices:
   * **Call audio from**: the speakers or headset you hear the call through.
   * **My microphone**: the mic you talk into.
3. Optionally type what the call is about (for example "Q4 planning with Acme,
   agree launch date") and who is on it (for example "Sarah, Tom from Acme").
   Claude uses both to write better notes and to put names to the speakers.
4. Press **Start recording** before or during the call. The **Call** bar moves when
   the other people talk and the **Mic** bar moves when you do. If either stays
   silent for a while, Scribe shows a red warning so you can fix the device
   before the call is lost.
5. Press **Stop and summarise** when the call ends. After a minute or two the
   notes open automatically. **Open transcript** and **Open folder** are there too.
6. If the transcript says "Speaker 1" and "Speaker 2", press **Name speakers...**,
   type who each one is (a few of their lines are shown to help), and press
   **Save**. The transcript and notes are redone with the names, without paying
   to transcribe again.

If something goes wrong after recording (no internet, a missing key), the audio is
kept. Fix the problem and use **Reprocess a session...** to pick the folder and try
again. It only pays for the steps that did not finish, and it reapplies the latest
speaker handling to the existing transcript.

### Tips for the best results

* **Wear headphones** if you can. With speakers, your mic also hears the other
  people. Scribe spots words that appear on both the call and your mic at the same
  moment and gives them to the right person, but headphones give the cleanest result.
* **The call has to play through your PC.** Scribe records what your PC plays, so a
  call made on your mobile is only heard faintly through your mic and everyone ends
  up labelled as you. Use the desktop app (Teams, Zoom, WhatsApp Desktop, Phone Link
  and so on) instead.
* Pick the device the call actually plays through. If you switch from speakers to a
  headset, choose the headset in **Call audio from** before you start.
* Everything else your PC plays (videos, notification sounds) is recorded too.
* For calls in another language, set `SCRIBE_LANGUAGE` in `.env` (for example
  `de`, `fr`, `es`), or `multi` for calls that mix languages.

### Command line (optional)

From the Scribe folder:

```powershell
.venv\Scripts\python -m scribe devices                 # list audio devices
.venv\Scripts\python -m scribe record "Weekly sync"    # record; Ctrl+C stops and processes
.venv\Scripts\python -m scribe process "C:\Users\you\Documents\Scribe\2026-10-20_0930_Weekly-sync"
```

## Settings

All settings live in `.env` (see `.env.example`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `DEEPGRAM_API_KEY` | | Transcription key (required) |
| `ANTHROPIC_API_KEY` | | Claude key (required) |
| `SCRIBE_USER_NAME` | `Me` | Your name in transcripts and action items |
| `SCRIBE_LANGUAGE` | `en` | Spoken language code, or `multi` |
| `SCRIBE_OUTPUT_DIR` | `Documents\Scribe` | Where sessions are saved |
| `SCRIBE_CLAUDE_MODEL` | `claude-opus-5-5` | Model used for the notes |
| `SCRIBE_KEEP_RAW_AUDIO` | `false` | Keep the original per-device recordings |

## Costs and privacy

* Deepgram bills per minute of audio, and each channel counts, so a call with your
  mic recorded costs about twice the mono rate. At current list prices that is
  roughly half a US dollar per hour of call; check
  [deepgram.com/pricing](https://deepgram.com/pricing).
* Claude typically costs a few tens of cents per hour-long call.
* Audio is sent to Deepgram and the transcript to Anthropic for processing. Nothing
  else leaves your PC, and all files stay in your Scribe folder.
* Recording laws differ by country and some calls require everyone's consent. Let
  people know you are recording.

## Development

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

The tests cover the audio mixdown, transcript parsing, the Claude request and the
full pipeline with the cloud calls faked, so they also run on macOS and Linux.
Only recording itself needs Windows.

| Module | Role |
| --- | --- |
| `scribe/audio.py` | WASAPI loopback and microphone capture (PyAudioWPatch) |
| `scribe/mixdown.py` | Aligns, resamples and merges the tracks into `recording.flac` |
| `scribe/transcribe.py` | Deepgram request, speaker labels, echo removal, transcript files |
| `scribe/summarize.py` | Claude prompt and request |
| `scribe/pipeline.py` | Session folders and the post-call steps |
| `scribe/gui.py` | The desktop window |
