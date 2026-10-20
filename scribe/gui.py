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

from . import audio, history, notes, pipeline, theme
from .config import APP_DIR, Settings

NO_MIC = "(don't record my microphone)"

# Warn while recording when a track has carried no sound for this long.
SILENCE_WARNING_SECONDS = 45

PAGES = ("Record", "Notes", "History")


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
        self.calls: list[history.PastCall] = []
        self.shown_calls: list[history.PastCall] = []
        self._search_after: str | None = None
        self._answer_started = False

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
        self._build_notes_page(self.pages["Notes"])
        self._build_history_page(self.pages["History"])
        self._show_page()

    def _show_page(self, name: str | None = None):
        if name:
            self.page_var.set(name)
        self.pages[self.page_var.get()].tkraise()
        if self.page_var.get() == "History":
            self._load_history()

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

        log_card = self._card(page, "Activity", row=3, column=0, columnspan=2, sticky="nsew",
                              pady=(14, 0))
        frame, self.log_text = theme.scrolled(
            log_card, lambda p: theme.text_area(p, self.palette, height=5, state="disabled",
                                                font=theme.CAPTION))
        frame.pack(fill="both", expand=True)
        page.rowconfigure(3, weight=1)

    def _build_notes_page(self, page: ttk.Frame):
        page.columnconfigure(0, weight=1)
        self.notes_title = ttk.Label(page, text="Notes", style="Title.TLabel", wraplength=760,
                                     justify="left")
        self.notes_title.grid(row=0, column=0, sticky="w")
        self.notes_caption = ttk.Label(page, text="", style="Muted.TLabel")
        self.notes_caption.grid(row=1, column=0, sticky="w", pady=(2, 14))

        bar = ttk.Frame(page)
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.copy_follow_up_button = ttk.Button(
            bar, text="Copy follow-up message", style="Accent.TButton", state="disabled",
            command=lambda: self._copy(notes.follow_up_message, "follow-up message"))
        self.copy_follow_up_button.pack(side="left")
        self.copy_actions_button = ttk.Button(
            bar, text="Copy action items", state="disabled",
            command=lambda: self._copy(notes.action_items_text, "action items"))
        self.copy_actions_button.pack(side="left", padx=6)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8, pady=4)
        self.summary_button = ttk.Button(bar, text="Open notes", state="disabled",
                                         command=lambda: self._open_file("summary.md"))
        self.summary_button.pack(side="left")
        self.transcript_button = ttk.Button(bar, text="Open transcript", state="disabled",
                                            command=lambda: self._open_file("transcript.md"))
        self.transcript_button.pack(side="left", padx=6)
        self.folder_button = ttk.Button(bar, text="Open folder", state="disabled",
                                        command=lambda: open_path(self.session_dir))
        self.folder_button.pack(side="left")
        self.names_button = ttk.Button(bar, text="Name speakers...", state="disabled",
                                       command=self._name_speakers)
        self.names_button.pack(side="left", padx=6)

        frame, self.notes_text = theme.scrolled(
            page, lambda p: theme.text_area(p, self.palette, padx=28, pady=20, cursor="arrow"))
        frame.grid(row=3, column=0, sticky="nsew")
        page.rowconfigure(3, weight=1)
        footer = ttk.Frame(page)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.copied_label = ttk.Label(footer, text="", style="Caption.TLabel")
        self.copied_label.pack(side="left")
        ttk.Button(footer, text="Reprocess a session...",
                   command=self._reprocess).pack(side="right")
        self._configure_note_tags(self.notes_text)
        self._show_markdown(self.notes_text, "No notes open yet. Record a call, or open "
                            "one from History.")

    def _build_history_page(self, page: ttk.Frame):
        page.columnconfigure(0, weight=1)
        ttk.Label(page, text="Call history", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.history_caption = ttk.Label(page, text="", style="Muted.TLabel")
        self.history_caption.grid(row=1, column=0, sticky="w", pady=(2, 14))

        search = ttk.Frame(page)
        search.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="Search").grid(row=0, column=0, padx=(0, 10))
        self.search_var = tk.StringVar()
        ttk.Entry(search, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        self.search_var.trace_add("write", lambda *_: self._schedule_search())
        self.search_count = ttk.Label(search, text="", style="Caption.TLabel")
        self.search_count.grid(row=0, column=2, padx=(10, 0))

        frame = ttk.Frame(page)
        frame.grid(row=3, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(frame, columns=("date", "title", "length"),
                                         show="headings", selectmode="browse", height=8)
        for column, heading, width, stretch in (("date", "Date", 150, False),
                                                ("title", "Call", 400, True),
                                                ("length", "Length", 90, False)):
            self.history_tree.heading(column, text=heading, anchor="w")
            self.history_tree.column(column, width=width, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=bar.set)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self.history_tree.bind("<Double-1>", lambda e: self._open_selected_call())
        self.history_tree.bind("<Return>", lambda e: self._open_selected_call())
        page.rowconfigure(3, weight=3)

        buttons = ttk.Frame(page)
        buttons.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Open notes", style="Accent.TButton",
                   command=self._open_selected_call).pack(side="left")
        ttk.Button(buttons, text="Open folder", command=lambda: self._open_selected_call(
            folder_only=True)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Reprocess a session...",
                   command=self._reprocess).pack(side="right")

        ask = self._card(page, "Ask about past calls", row=5, column=0, sticky="nsew",
                         pady=(16, 0))
        self.ask_caption = ttk.Label(ask, text="", style="Caption.TLabel")
        self.ask_caption.pack(anchor="w")
        row = ttk.Frame(ask)
        row.pack(fill="x", pady=(6, 8))
        self.question_var = tk.StringVar()
        question = ttk.Entry(row, textvariable=self.question_var)
        question.pack(side="left", fill="x", expand=True)
        question.bind("<Return>", lambda e: self._ask())
        self.ask_button = ttk.Button(row, text="Ask", style="Accent.TButton", command=self._ask)
        self.ask_button.pack(side="left", padx=(8, 0))
        frame, self.answer_text = theme.scrolled(
            ask, lambda p: theme.text_area(p, self.palette, height=7, state="disabled"))
        frame.pack(fill="both", expand=True)
        self._configure_note_tags(self.answer_text)
        page.rowconfigure(5, weight=2)

    def _load_history(self):
        self.settings = Settings.load()
        self.calls = history.list_calls(self.settings.output_dir)
        self.history_caption.configure(text=f"Calls saved in {self.settings.output_dir}")
        self._filter_history()

    def _schedule_search(self):
        if self._search_after:
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(250, self._filter_history)

    def _filter_history(self):
        self._search_after = None
        query = self.search_var.get()
        self.shown_calls = history.search(self.calls, query)
        tree = self.history_tree
        tree.delete(*tree.get_children())
        for i, call in enumerate(self.shown_calls):
            length = pipeline._duration_text(call.duration_seconds) if call.duration_seconds else ""
            tree.insert("", "end", iid=str(i), values=(call.started, call.title, length))
        n, total = len(self.shown_calls), len(self.calls)
        plural = lambda k: "" if k == 1 else "s"  # noqa: E731
        self.search_count.configure(
            text=f"{n} of {total}" if query.strip() else f"{total} call{plural(total)}")
        scope = f"the {n} matching call{plural(n)}" if query.strip() else "your calls"
        self.ask_caption.configure(text=f"Claude reads {scope}, newest first, and answers from "
                                   "their notes and transcripts. Search first to narrow it down.")

    def _open_selected_call(self, folder_only: bool = False):
        selected = self.history_tree.selection()
        if not selected:
            if len(self.shown_calls) != 1:
                return
            selected = ("0",)
        call = self.shown_calls[int(selected[0])]
        if folder_only:
            open_path(call.folder)
        else:
            self.show_notes(call.folder)

    def _ask(self):
        question = self.question_var.get().strip()
        if not question or str(self.ask_button.cget("state")) == "disabled":
            return
        self.settings = Settings.load()
        if not self.settings.anthropic_api_key:
            self._show_markdown(self.answer_text, "Add ANTHROPIC_API_KEY to "
                                f"{APP_DIR / '.env'} to ask about past calls.")
            return
        if not self.shown_calls:
            self._show_markdown(self.answer_text, "There are no calls to ask about.")
            return
        calls = list(self.shown_calls)
        self.ask_button.configure(state="disabled")
        self._show_markdown(self.answer_text, "Reading your calls...")
        self._answer_started = False
        settings = self.settings

        def work():
            try:
                answer = history.ask(question, calls, settings,
                                     on_text=lambda t: self.events.put(("ask_text", t)))
                self.events.put(("ask_done", answer))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("ask_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _configure_note_tags(self, text: tk.Text):
        p = self.palette
        text.tag_configure("h1", font=theme.TITLE, spacing1=2, spacing3=10)
        text.tag_configure("h2", font=theme.SUBTITLE, spacing1=18, spacing3=6)
        text.tag_configure("h3", font=theme.STRONG, spacing1=12, spacing3=4)
        text.tag_configure("p", spacing1=2, spacing3=6)
        text.tag_configure("bullet", lmargin1=8, lmargin2=26, spacing1=2, spacing3=2)
        text.tag_configure("bullet2", lmargin1=30, lmargin2=48, spacing1=1, spacing3=1)
        text.tag_configure("item", lmargin1=8, lmargin2=8, spacing1=8)
        text.tag_configure("meta", lmargin1=8, lmargin2=8, foreground=p.muted,
                           font=theme.CAPTION, spacing3=2)
        text.tag_configure("quote", lmargin1=22, lmargin2=22, rmargin=22, foreground=p.fg,
                           background=p.bg, spacing1=8, spacing3=8)
        text.tag_configure("bold", font=theme.STRONG)
        text.tag_configure("italic", font=theme.ITALIC)
        text.tag_configure("code", font=("Cascadia Mono", 10))
        text.tag_raise("bold")

    def _show_markdown(self, text: tk.Text, md: str):
        text.configure(state="normal")
        text.delete("1.0", "end")
        for chunk, tags in notes.render(md):
            text.insert("end", chunk, tags)
        text.configure(state="disabled")
        text.yview_moveto(0)

    def show_notes(self, folder: Path):
        """Show a session's notes in the window."""
        self.session_dir = folder
        summary = folder / "summary.md"
        md = summary.read_text(encoding="utf-8") if summary.exists() else ""
        self.notes_md = md
        try:
            meta = pipeline.SessionMeta.load(folder)
            caption = meta.started
            if meta.duration_seconds:
                caption += f"   {pipeline._duration_text(meta.duration_seconds)}"
        except (OSError, ValueError, TypeError):
            caption = ""
        self.notes_title.configure(text=notes.title(md) or folder.name)
        self.notes_caption.configure(text=caption)
        body = md.split("\n", 1)[1] if md.startswith("# ") else md
        self._show_markdown(self.notes_text, body or "This call has no notes yet. Use "
                            "'Reprocess a session' to write them.")
        has = {name: (folder / name).exists() for name in ("summary.md", "transcript.md",
                                                           "transcript.json")}
        state = lambda ok: "normal" if ok else "disabled"  # noqa: E731
        self.summary_button.configure(state=state(has["summary.md"]))
        self.transcript_button.configure(state=state(has["transcript.md"]))
        self.names_button.configure(state=state(has["transcript.json"]))
        self.folder_button.configure(state="normal")
        self.copy_follow_up_button.configure(state=state(bool(notes.follow_up_message(md))))
        self.copy_actions_button.configure(state=state(bool(notes.action_items_text(md))))
        self.copied_label.configure(text="")
        self._show_page("Notes")

    def _copy(self, extract, what: str):
        text = extract(getattr(self, "notes_md", ""))
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.copied_label.configure(text=f"Copied the {what}. Paste it into your email or chat.")

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
        self._log("Ready. Press the record button before or during your call, "
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
        elif kind == "ask_text":
            # Show the answer as it streams in, then lay it out once it is complete.
            self.answer_text.configure(state="normal")
            if not self._answer_started:
                self.answer_text.delete("1.0", "end")
                self._answer_started = True
            self.answer_text.insert("end", payload, ("p",))
            self.answer_text.see("end")
            self.answer_text.configure(state="disabled")
        elif kind == "ask_done":
            self.ask_button.configure(state="normal")
            self._show_markdown(self.answer_text, payload)
        elif kind == "ask_error":
            self.ask_button.configure(state="normal")
            self._show_markdown(self.answer_text, f"Could not get an answer: {payload}")
        elif kind == "done":
            self._finish_processing()
            self.show_notes(payload)
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
