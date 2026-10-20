"""The Scribe desktop window (tkinter)."""

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

from . import audio, pipeline
from .config import APP_DIR, Settings

NO_MIC = "(don't record my microphone)"

# Warn while recording when a track has carried no sound for this long.
SILENCE_WARNING_SECONDS = 45


def open_path(path: Path):
    if sys.platform == "win32":
        try:
            os.startfile(str(path))  # noqa: S606
        except OSError:
            # No app is associated with .md files on a fresh Windows install.
            subprocess.Popen(["notepad.exe", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ScribeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.recorder: audio.Recorder | None = None
        self.session_dir: Path | None = None
        self.meta: pipeline.SessionMeta | None = None
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        root.title("Scribe")
        root.geometry("680x640")
        root.minsize(660, 560)
        self._build()
        self._load_devices()
        self._check_keys()
        self.root.after(100, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # UI layout

    def _build(self):
        pad = {"padx": 12, "pady": 4}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, pady=8)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Call audio from").grid(row=0, column=0, sticky="w", **pad)
        self.output_var = tk.StringVar()
        self.output_box = ttk.Combobox(frame, textvariable=self.output_var, state="readonly")
        self.output_box.grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(frame, text="My microphone").grid(row=1, column=0, sticky="w", **pad)
        self.mic_var = tk.StringVar()
        self.mic_box = ttk.Combobox(frame, textvariable=self.mic_var, state="readonly")
        self.mic_box.grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(frame, text="What is this call about?\n(optional, helps the summary)",
                  justify="left").grid(row=2, column=0, sticky="nw", **pad)
        self.context_text = tk.Text(frame, height=4, wrap="word")
        self.context_text.grid(row=2, column=1, sticky="ew", **pad)

        ttk.Label(frame, text="Who's on the call?\n(optional, e.g. Sarah, Tom from Acme)",
                  justify="left").grid(row=3, column=0, sticky="nw", **pad)
        self.participants_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.participants_var).grid(
            row=3, column=1, sticky="ew", **pad)

        meters = ttk.Frame(frame)
        meters.grid(row=4, column=0, columnspan=2, sticky="ew", **pad)
        meters.columnconfigure(1, weight=1)
        ttk.Label(meters, text="Call").grid(row=0, column=0, sticky="w")
        self.system_meter = ttk.Progressbar(meters, maximum=1.0)
        self.system_meter.grid(row=0, column=1, sticky="ew", padx=8, pady=2)
        ttk.Label(meters, text="Mic").grid(row=1, column=0, sticky="w")
        self.mic_meter = ttk.Progressbar(meters, maximum=1.0)
        self.mic_meter.grid(row=1, column=1, sticky="ew", padx=8, pady=2)
        self.warning_label = ttk.Label(meters, text="", foreground="#b00020", wraplength=480,
                                       justify="left")
        self.warning_label.grid(row=2, column=0, columnspan=2, sticky="w")

        controls = ttk.Frame(frame)
        controls.grid(row=5, column=0, columnspan=2, sticky="ew", **pad)
        self.record_button = ttk.Button(controls, text="Start recording", command=self._toggle)
        self.record_button.pack(side="left")
        self.timer_label = ttk.Label(controls, text="00:00:00", font=("Segoe UI", 14))
        self.timer_label.pack(side="left", padx=16)

        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", **pad)
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

        self.log_text = tk.Text(frame, height=10, wrap="word", state="disabled",
                                background="#f4f4f4", relief="flat")
        self.log_text.grid(row=7, column=0, columnspan=2, sticky="nsew", **pad)
        frame.rowconfigure(7, weight=1)

    def _load_devices(self):
        try:
            outputs, mics, default_out, default_mic = audio.list_devices()
        except Exception as exc:  # noqa: BLE001
            self._log(f"Could not list audio devices: {exc}")
            self.record_button.configure(state="disabled")
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
        self.record_button.configure(text="Stop and summarise")
        self.output_box.configure(state="disabled")
        self.mic_box.configure(state="disabled")
        for button in (self.summary_button, self.transcript_button, self.names_button):
            button.configure(state="disabled")
        self.folder_button.configure(state="normal")
        self._log(f"Recording to {self.session_dir}")

    def _stop(self):
        self._save_meta(self.recorder.stop())
        self.recorder = None
        self.record_button.configure(text="Start recording")
        self.output_box.configure(state="readonly")
        self.mic_box.configure(state="readonly")
        self.system_meter["value"] = 0
        self.mic_meter["value"] = 0
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
        self.record_button.configure(state="disabled")
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
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Type a name for each speaker, then press Save. "
                  "The transcript and notes are redone with the names.",
                  wraplength=460, justify="left").grid(row=0, column=0, columnspan=2,
                                                      sticky="w", pady=(0, 8))
        entries = {}
        for row, (label, lines) in enumerate(samples.items(), start=1):
            ttk.Label(body, text=label, font=("Segoe UI", 10, "bold")).grid(
                row=row * 2 - 1, column=0, sticky="w", pady=(8, 0))
            var = tk.StringVar(value=meta.speaker_names.get(label, ""))
            ttk.Entry(body, textvariable=var).grid(row=row * 2 - 1, column=1, sticky="ew",
                                                   padx=(8, 0), pady=(8, 0))
            quote = "\n".join(f"\u201c{line}\u201d" for line in lines)
            ttk.Label(body, text=quote, foreground="#555", wraplength=460,
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
        buttons.grid(row=len(samples) * 2 + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=6)
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
            elapsed = int(self.recorder.elapsed())
            self.timer_label.configure(
                text=f"{elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}")
            levels = self.recorder.levels()
            self.system_meter["value"] = levels.get("system", 0)
            self.mic_meter["value"] = levels.get("mic", 0)
            self.warning_label.configure(text=self._silence_warning())
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "done":
                self.busy = False
                self.record_button.configure(state="normal")
                self.summary_button.configure(state="normal")
                self.transcript_button.configure(state="normal")
                self.names_button.configure(state="normal")
                self._open_file("summary.md")
            elif kind == "error":
                self.busy = False
                self.record_button.configure(state="normal")
                if self.session_dir and (self.session_dir / "transcript.md").exists():
                    self.transcript_button.configure(state="normal")
                self._log(f"Problem: {payload}\nThe recording is saved; fix the issue and "
                          "use 'Reprocess a session' to try again.")
        self.root.after(100, self._tick)

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
