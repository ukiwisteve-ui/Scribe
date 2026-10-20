"""Windows 11 styling: Sun Valley ttk theme, light/dark mode and custom widgets."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk

try:
    import sv_ttk
except ImportError:  # Falls back to the plain ttk look.
    sv_ttk = None


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str  # text areas
    border: str
    fg: str
    muted: str
    accent: str
    record: str
    record_hover: str
    meter_track: str
    meter_low: str
    meter_high: str
    selection: str


DARK = Palette(bg="#1c1c1c", surface="#262626", border="#383838", fg="#fafafa",
               muted="#a8a8a8", accent="#57c8ff", record="#e0383b", record_hover="#f05a5c",
               meter_track="#333333", meter_low="#6ccb5f", meter_high="#fce100",
               selection="#2f60d8")
LIGHT = Palette(bg="#fafafa", surface="#ffffff", border="#e0e0e0", fg="#1c1c1c",
                muted="#5f5f5f", accent="#005fb8", record="#c42b1c", record_hover="#d64536",
                meter_track="#e3e3e3", meter_low="#0f7b0f", meter_high="#9d5d00",
                selection="#cce4f7")

BODY = "SunValleyBodyFont"
STRONG = "SunValleyBodyStrongFont"
CAPTION = "SunValleyCaptionFont"
SUBTITLE = "SunValleySubtitleFont"
TITLE = "SunValleyTitleFont"
ITALIC = "ScribeItalicFont"


def windows_prefers_dark() -> bool:
    """Whether Windows is set to dark mode for apps."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_dark_title_bar(root: tk.Tk, dark: bool):
    """Match the window's title bar to the theme (Windows 10 20H1 and later)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 (19 on early Windows 10 builds).
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def apply(root: tk.Tk) -> Palette:
    dark = windows_prefers_dark()
    palette = DARK if dark else LIGHT
    if sv_ttk is not None:
        sv_ttk.set_theme("dark" if dark else "light", root)
        # sv-ttk sets the base colours from a <<ThemeChanged>> binding that does
        # not always fire on newer Tk builds, so run it directly.
        try:
            root.tk.call("configure_colors")
        except tk.TclError:
            pass
    else:
        for name in (BODY, STRONG, CAPTION, SUBTITLE, TITLE):
            size = {CAPTION: -12, SUBTITLE: -20, TITLE: -28}.get(name, -14)
            weight = "bold" if name in (STRONG, SUBTITLE, TITLE) else "normal"
            root.tk.call("font", "create", name, "-family", "Segoe UI", "-size", size,
                         "-weight", weight)
    # The variable Segoe font has no italic face in Tk, so use classic Segoe UI.
    # Keep a reference: tkinter deletes a named font when its Font object is collected.
    root.scribe_italic_font = tkfont.Font(root, name=ITALIC, family="Segoe UI", size=-14,
                                          slant="italic")
    root.configure(background=palette.bg)
    style = ttk.Style(root)
    style.configure("Muted.TLabel", foreground=palette.muted)
    style.configure("Caption.TLabel", foreground=palette.muted, font=CAPTION)
    style.configure("Strong.TLabel", font=STRONG)
    style.configure("Subtitle.TLabel", font=SUBTITLE)
    style.configure("Title.TLabel", font=TITLE)
    style.configure("Timer.TLabel", font=TITLE)
    style.configure("Warning.TLabel", foreground=palette.record)
    style.configure("Nav.Toolbutton", font=BODY, padding=(14, 8), anchor="w")
    style.map("Nav.Toolbutton", foreground=[("selected", palette.accent)],
              font=[("selected", STRONG)])
    style.configure("Treeview", rowheight=30)
    set_dark_title_bar(root, dark)
    return palette


def text_area(parent, palette: Palette, **kwargs) -> tk.Text:
    """A tk.Text that matches the theme."""
    options = dict(wrap="word", relief="flat", borderwidth=0, highlightthickness=1,
                   highlightbackground=palette.border, highlightcolor=palette.accent,
                   background=palette.surface, foreground=palette.fg,
                   insertbackground=palette.fg, selectbackground=palette.selection,
                   font=BODY, padx=12, pady=10)
    options.update(kwargs)
    return tk.Text(parent, **options)


def scrolled(parent, widget_factory):
    """Put a widget and a vertical scrollbar in a frame. Returns (frame, widget)."""
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    widget = widget_factory(frame)
    bar = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
    widget.configure(yscrollcommand=bar.set)
    widget.grid(row=0, column=0, sticky="nsew")
    bar.grid(row=0, column=1, sticky="ns")
    return frame, widget


class RecordButton(tk.Canvas):
    """A large round record button: a red dot to start, a white square to stop."""

    def __init__(self, parent, palette: Palette, command, size: int = 88):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         background=palette.bg, cursor="hand2")
        self.palette = palette
        self.command = command
        self.size = size
        self.recording = False
        self.enabled = True
        self.hover = False
        self.pulse = 0
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def _set_hover(self, hover: bool):
        self.hover = hover
        self._draw()

    def _click(self, _event):
        if self.enabled:
            self.command()

    def set_state(self, recording: bool | None = None, enabled: bool | None = None):
        if recording is not None:
            self.recording = recording
        if enabled is not None:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def tick(self):
        """Animate the ring while recording (call about ten times a second)."""
        if self.recording:
            self.pulse = (self.pulse + 1) % 20
            self._draw()

    def _draw(self):
        self.delete("all")
        s, p = self.size, self.palette
        fill = p.meter_track if not self.enabled else (p.record_hover if self.hover else p.record)
        if self.recording:
            # A soft ring that breathes, so it is obvious the call is being recorded.
            phase = abs(10 - self.pulse) / 10
            inset = 1 + phase * 4
            self.create_oval(inset, inset, s - inset, s - inset, outline=p.record, width=2)
        m = 8
        self.create_oval(m, m, s - m, s - m, fill=fill, outline="")
        c = s / 2
        if self.recording:
            r = s * 0.14
            self._rounded_rect(c - r, c - r, c + r, c + r, 4, fill="#ffffff")
        else:
            r = s * 0.13
            self.create_oval(c - r, c - r, c + r, c + r, fill="#ffffff", outline="")

    def _rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                  x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(points, smooth=True, outline="", **kwargs)


class LevelMeter(tk.Canvas):
    """A rounded level bar with a falling peak, like the Windows sound settings."""

    def __init__(self, parent, palette: Palette, height: int = 8):
        super().__init__(parent, height=height, highlightthickness=0, background=palette.bg)
        self.palette = palette
        self.level = 0.0
        self.shown = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, level: float):
        self.level = max(0.0, min(1.0, level))
        # Rise quickly, fall slowly, so speech reads as a steady bar.
        self.shown = self.level if self.level > self.shown else self.shown * 0.8 + self.level * 0.2
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self.cget("height"))
        if w <= 1:
            return
        p = self.palette
        self._bar(0, w, h, p.meter_track)
        filled = int(w * self.shown)
        if filled > h:
            self._bar(0, filled, h, p.meter_high if self.shown > 0.85 else p.meter_low)

    def _bar(self, x1, x2, h, colour):
        r = h / 2
        self.create_oval(x1, 0, x1 + h, h, fill=colour, outline="")
        self.create_oval(x2 - h, 0, x2, h, fill=colour, outline="")
        self.create_rectangle(x1 + r, 0, x2 - r, h, fill=colour, outline="")
