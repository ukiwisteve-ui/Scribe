"""The Scribe desktop window (tkinter with the Windows 11 Sun Valley theme)."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import audio, pipeline, theme
from .config import APP_DIR, Settings

NO_MIC = "(don't record my microphone)"

# Warn while recording when a track has carried no sound for this long.
SILENCE_WARNING_SECONDS = 45

PAGES = ("Record",)


def open_path(path: Path):
    if sys.platform == "win32":
        try:
            os.startfile(str(path))  # noqa: S606
        except OSError:
            # No app is associated with .md files on a fresh Windows install.
            subprocess.Popen(["notepad.exe", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


class ScribeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.palette = theme.apply(root)
        self.settings = Settings.load()
        self.recorder: audio.Recorder | None = None
        self.session_dir: Path | None = None
        self.meta: pipeline.SessionMeta | None = None
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.outputs: dict[str, audio.Device] = {}
        self.mics: dict[str, audio.Device] = {}

        root.title("Scribe")
        root.geometry("1040x760")
        root.minsize(900, 640)
        self._build()
        self._load_devices()
        self._check_keys()
        self.root.after(100, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # UI layout

    def _build(self):
        self.root.columnconfigure(2, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=(12, 20, 12, 12), width=200)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="Scribe", style="Subtitle.TLabel").pack(anchor="w", padx=8)
        ttk.Label(sidebar, text="Call notes, written for you", style="Caption.TLabel").pack(
            anchor="w", padx=8, pady=(0, 18))
        self.page_var = tk.StringVar(value=PAGES[0])
        self.nav: dict[str, ttk.Radiobutton] = {}
        for name in PAGES:
            button = ttk.Radiobutton(sidebar, text=name, value=name, variable=self.page_var,
                                     style="Nav.Toolbutton", command=self._show_page)
            button.pack(fill="x", pady=2)
            self.nav[name] = button
        ttk.Separator(self.root, orient="vertical").grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(self.root)
        content.grid(row=0, column=2, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.pages: dict[str, ttk.Frame] = {}
        for name in PAGES:
            page = ttk.Frame(content, padding=(28, 20, 28, 20))
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self._build_record_page(self.pages["Record"])
        self._show_page()

    def _show_page(self, name: str | None = None):
        if name:
            self.page_var.set(name)
        self.pages[self.page_var.get()].tkraise()

    def _card(self, parent, title: str, **grid) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(18, 14, 18, 16))
        card.grid(**grid)
        ttk.Label(card, text=title, style="Strong.TLabel").pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(card)
        body.pack(fill="both", expand=True)
        return body

    def _field_label(self, parent, text: str, hint: str = ""):
        ttk.Label(parent, text=text).pack(anchor="w", pady=(6, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Caption.TLabel").pack(anchor="w")

    def _build_record_page(self, page: ttk.Frame):
        page.columnconfigure(0, weight=1)
        ttk.Label(page, text="Record a call", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(page, text="Works with Teams, Zoom, Meet, WhatsApp or anything else your PC "
                  "plays. Start before or during the call.", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 16))

        setup = self._card(page, "Call setup", row=2, column=0, sticky="nsew", padx=(0, 14))
        self._field_label(setup, "Call audio from", "The speakers or headset you hear the call through")
        self.output_var = tk.StringVar()
        self.output_box = ttk.Combobox(setup, textvariable=self.output_var, state="readonly")
        self.output_box.pack(fill="x", pady=(4, 6))
        self._field_label(setup, "My microphone")
        self.mic_var = tk.StringVar()
        self.mic_box = ttk.Combobox(setup, textvariable=self.mic_var, state="readonly")
        self.mic_box.pack(fill="x", pady=(4, 6))
        self._field_label(setup, "What is this call about?", "Optional, helps the summary")
        self.context_text = theme.text_area(setup, self.palette, height=3)
        self.context_text.pack(fill="x", pady=(4, 6))
        self._field_label(setup, "Who's on the call?",
                          "Optional, for example Sarah, Tom from Acme")
        self.participants_var = tk.StringVar()
        ttk.Entry(setup, textvariable=self.participants_var).pack(fill="x", pady=(4, 0))

        rec = self._card(page, "Recording", row=2, column=1, sticky="nsew")
        rec.configure(width=290)
        self.record_button = theme.RecordButton(rec, self.palette, self._toggle)
        self.record_button.pack(pady=(4, 6))
        self.status_label = ttk.Label(rec, text="Ready to record", style="Strong.TLabel")
        self.status_label.pack()
        self.timer_label = ttk.Label(rec, text="00:00:00", style="Timer.TLabel")
        self.timer_label.pack(pady=(0, 10))
        meters = ttk.Frame(rec)
        meters.pack(fill="x")
        meters.columnconfigure(1, weight=1)
        ttk.Label(meters, text="Call", style="Caption.TLabel").grid(row=0, column=0, sticky="w")
        self.system_meter = theme.LevelMeter(meters, self.palette)
        self.system_meter.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6)
        ttk.Label(meters, text="Mic", style="Caption.TLabel").grid(row=1, column=0, sticky="w")
        self.mic_meter = theme.LevelMeter(meters, self.palette)
        self.mic_meter.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)
        self.warning_label = ttk.Label(rec, text="", style="Warning.TLabel", wraplength=250,
                                       justify="left")
        self.warning_label.pack(fill="x", pady=(6, 0))

        actions = ttk.Frame(page)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.summary_button = ttk.Button(actions, text="Open notes", state="disabled",
                                         command=lambda: self._open_file("summary.md"))
        self.summary_button.pack(side="left")
        self.transcript_button = ttk.Button(actions, text="Open transcript", state="disabled",
                                            command=lambda: self._open_file("transcript.md"))
        self.transcript_button.pack(side="left", padx=6)
        self.folder_button = ttk.Button(actions, text="Open folder", state="disabled",
                                        command=lambda: open_path(self.session_dir))
        self.folder_button.pack(side="left")
        self.names_button = ttk.Button(actions, text="Name speakers...", state="disabled",
                                       command=self._name_speakers)
        self.names_button.pack(side="left", padx=6)
        ttk.Button(actions, text="Reprocess a session...",
                   command=self._reprocess).pack(side="right")

        log_card = self._card(page, "Activity", row=4, column=0, columnspan=2, sticky="nsew",
                              pady=(14, 0))
        frame, self.log_text = theme.scrolled(
            log_card, lambda p: theme.text_area(p, self.palette, height=5, state="disabled",
                                                font=theme.CAPTION))
        frame.pack(fill="both", expand=True)
        page.rowconfigure(4, weight=1)

    def _load_devices(self):
        try:
            outputs, mics, default_out, default_mic = audio.list_devices()
        except Exception as exc:  # noqa: BLE001
            self._log(f"Could not list audio devices: {exc}")
            self.record_button.set_state(enabled=False)
            return
        self.outputs = {d.name: d for d in outputs}
        self.mics = {d.name: d for d in mics}
        self.output_box["values"] = list(self.outputs)
        self.mic_box["values"] = [NO_MIC, *self.mics]
        if default_out:
            self.output_var.set(default_out.name)
        self.mic_var.set(default_mic.name if default_mic else NO_MIC)
        self._log("Ready. Start recording before or during your call, "
                  "then press Stop when it ends.")

    def _check_keys(self):
        missing = self.settings.missing_keys()
        if missing:
            self._log(f"Setup needed: add {', '.join(missing)} to {APP_DIR / '.env'} "
                      "and restart Scribe. You can still record now and process later.")

    # Recording

    def _toggle(self):
        if self.recorder is None:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.busy:
            return
        output = self.outputs.get(self.output_var.get())
        if output is None:
            messagebox.showerror("Scribe", "Choose the device your call audio plays through.")
            return
        mic = self.mics.get(self.mic_var.get())
        context = self.context_text.get("1.0", "end").strip()
        self.session_dir = pipeline.new_session_dir(self.settings, context)
        self.meta = pipeline.SessionMeta(started=datetime.now().strftime("%Y-%m-%d %H:%M"))
        try:
            self.recorder = audio.Recorder(self.session_dir, output, mic)
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001
            self.recorder = None
            messagebox.showerror("Scribe", f"Could not start recording:\n{exc}")
            return
        self.record_button.set_state(recording=True)
        self.status_label.configure(text="Recording. Press to stop and summarise")
        self.output_box.configure(state="disabled")
        self.mic_box.configure(state="disabled")
        for button in (self.summary_button, self.transcript_button, self.names_button):
            button.configure(state="disabled")
        self.folder_button.configure(state="normal")
        self._log(f"Recording to {self.session_dir}")

    def _stop(self):
        self._save_meta(self.recorder.stop())
        self.recorder = None
        self.record_button.set_state(recording=False)
        self.status_label.configure(text="Ready to record")
        self.output_box.configure(state="readonly")
        self.mic_box.configure(state="readonly")
        self.system_meter.set(0)
        self.mic_meter.set(0)
        self.warning_label.configure(text="")
        self._log("Recording stopped.")
        self._process(self.session_dir)

    def _save_meta(self, tracks: dict):
        self.meta.context = self.context_text.get("1.0", "end").strip()
        self.meta.participants = self.participants_var.get().strip()
        self.meta.tracks = pipeline.tracks_to_meta(tracks)
        self.meta.save(self.session_dir)

    # Processing

    def _process(self, folder: Path):
        self.busy = True
        self.record_button.set_state(enabled=False)
        self.status_label.configure(text="Writing your notes...")
        self.settings = Settings.load()

        def work():
            try:
                pipeline.process_session(folder, self.settings,
                                         log=lambda m: self.events.put(("log", m)))
                self.events.put(("done", folder))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _reprocess(self):
        if self.recorder is not None or self.busy:
            return
        chosen = filedialog.askdirectory(initialdir=str(self.settings.output_dir),
                                         title="Choose a session folder")
        if not chosen:
            return
        folder = Path(chosen)
        if not (folder / pipeline.SESSION_FILE).exists():
            messagebox.showerror("Scribe", "That folder is not a Scribe session.")
            return
        self.session_dir = folder
        self.folder_button.configure(state="normal")
        self._log(f"Reprocessing {folder}")
        self._process(folder)

    def _name_speakers(self):
        if self.busy or not self.session_dir:
            return
        folder = self.session_dir
        samples = pipeline.speaker_samples(folder)
        if not samples:
            return
        meta = pipeline.SessionMeta.load(folder)

        dialog = tk.Toplevel(self.root)
        dialog.title("Name speakers")
        dialog.transient(self.root)
        dialog.configure(background=self.palette.bg)
        theme.set_dark_title_bar(dialog, self.palette is theme.DARK)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Type a name for each speaker, then press Save. "
                  "The transcript and notes are redone with the names.",
                  wraplength=460, justify="left").grid(row=0, column=0, columnspan=2,
                                                      sticky="w", pady=(0, 8))
        entries = {}
        for row, (label, lines) in enumerate(samples.items(), start=1):
            ttk.Label(body, text=label, style="Strong.TLabel").grid(
                row=row * 2 - 1, column=0, sticky="w", pady=(10, 0))
            var = tk.StringVar(value=meta.speaker_names.get(label, ""))
            ttk.Entry(body, textvariable=var).grid(row=row * 2 - 1, column=1, sticky="ew",
                                                   padx=(10, 0), pady=(10, 0))
            quote = "\n".join(f"“{line}”" for line in lines)
            ttk.Label(body, text=quote, style="Muted.TLabel", wraplength=460,
                      justify="left").grid(row=row * 2, column=0, columnspan=2, sticky="w")
            entries[label] = var

        def save():
            meta.speaker_names = {label: var.get().strip() for label, var in entries.items()
                                  if var.get().strip() and var.get().strip() != label}
            meta.save(folder)
            dialog.destroy()
            self._log("Updating the transcript and notes with the new names...")
            self._process(folder)

        buttons = ttk.Frame(body)
        buttons.grid(row=len(samples) * 2 + 1, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save).pack(
            side="right", padx=6)
        dialog.grab_set()

    def _silence_warning(self) -> str:
        silent = self.recorder.silent_for()
        warnings = []
        if silent.get("system", 0) > SILENCE_WARNING_SECONDS:
            warnings.append("No sound from the call for a while. If people are talking, check "
                            "that 'Call audio from' is the speakers or headset you hear them "
                            "through. Calls taken on your phone can't be recorded.")
        if silent.get("mic", 0) > SILENCE_WARNING_SECONDS:
            warnings.append("Your microphone has been silent for a while. Check 'My microphone' "
                            "and that you aren't muted.")
        return "\n".join(warnings)

    def _open_file(self, name: str):
        path = self.session_dir / name if self.session_dir else None
        if path and path.exists():
            open_path(path)

    # Event loop

    def _tick(self):
        if self.recorder is not None:
            self.timer_label.configure(text=format_elapsed(self.recorder.elapsed()))
            levels = self.recorder.levels()
            self.system_meter.set(levels.get("system", 0))
            self.mic_meter.set(levels.get("mic", 0))
            self.warning_label.configure(text=self._silence_warning())
            self.record_button.tick()
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(kind, payload)
        self.root.after(100, self._tick)

    def _handle_event(self, kind: str, payload):
        if kind == "log":
            self._log(payload)
        elif kind == "done":
            self._finish_processing()
            self.summary_button.configure(state="normal")
            self.transcript_button.configure(state="normal")
            self.names_button.configure(state="normal")
            self._open_file("summary.md")
        elif kind == "error":
            self._finish_processing()
            if self.session_dir and (self.session_dir / "transcript.md").exists():
                self.transcript_button.configure(state="normal")
            self._log(f"Problem: {payload}\nThe recording is saved; fix the issue and "
                      "use 'Reprocess a session' to try again.")

    def _finish_processing(self):
        self.busy = False
        self.record_button.set_state(enabled=True)
        self.status_label.configure(text="Ready to record")

    def _log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        if self.recorder is not None:
            if not messagebox.askyesno("Scribe", "Recording in progress. Stop and quit? "
                                       "The audio is kept and can be processed later."):
                return
            self._save_meta(self.recorder.stop())
        self.root.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    ScribeApp(root)
    root.mainloop()
