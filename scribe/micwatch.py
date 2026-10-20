"""Notice when another app starts using the microphone, so Scribe can offer to record.

Windows records microphone use per app under
HKCU\\...\\CapabilityAccessManager\\ConsentStore\\microphone: Store apps (Teams,
WhatsApp) as subkeys, desktop apps under NonPackaged as their exe path with "#"
for "\\". While an app is using the mic, LastUsedTimeStop is 0.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CONSENT_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

# A crashed app can stay marked as using the mic for ever, so only mic use that
# started recently counts. When Scribe opens, a call that started up to this
# long ago still gets the prompt.
STARTUP_GRACE_SECONDS = 15 * 60

# Friendly names for common call apps, matched against the exe or package name.
KNOWN_APPS = {
    "msteams": "Teams", "ms-teams": "Teams", "teams": "Teams", "zoom": "Zoom",
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave",
    "opera": "Opera", "slack": "Slack", "whatsappdesktop": "WhatsApp", "whatsapp": "WhatsApp",
    "discord": "Discord", "skype": "Skype", "telegram": "Telegram", "signal": "Signal",
    "webex": "Webex", "ciscocollabhost": "Webex", "yourphone": "Phone Link",
    "phoneexperiencehost": "Phone Link", "ringcentral": "RingCentral", "gotomeeting": "GoTo",
}

# Apps that use the mic for things other than calls.
NOT_CALLS = {"windowssoundrecorder", "windowscamera", "screensketch", "xboxgamingoverlay",
             "immersivecontrolpanel", "mfaudiolocal", "mfaudiort", "rtaivc"}


@dataclass(frozen=True)
class MicUse:
    key: str  # registry subkey, e.g. "MSTeams_8wekyb3d8bbwe" or "C:#...#Zoom.exe"
    start: int  # LastUsedTimeStart as a Windows FILETIME

    @property
    def app(self) -> str:
        return app_name(self.key)


def filetime_now() -> int:
    return int((time.time() + 11644473600) * 10_000_000)


def _app_id(key: str) -> str:
    """"C:#Users#x#Zoom#bin#Zoom.exe" -> "zoom", "MSTeams_8wekyb3d8bbwe" -> "msteams"."""
    if "#" in key:
        name = Path(key.replace("#", "\\")).stem
    else:
        name = key.split("_")[0].split(".")[-1]
    return name.lower()


def app_name(key: str) -> str:
    app = _app_id(key)
    if app in KNOWN_APPS:
        return KNOWN_APPS[app]
    if "#" in key:
        return Path(key.replace("#", "\\")).stem
    # "5319275A.WhatsAppDesktop_cv1g1gvanyjgm" -> "WhatsAppDesktop"
    return key.split("_")[0].split(".")[-1]


def own_keys() -> set[str]:
    """Registry names Scribe itself appears under (its Python, in or out of the venv)."""
    paths = {Path(sys.executable)}
    base = getattr(sys, "_base_executable", None)
    if base:
        paths.add(Path(base))
    keys = set()
    for path in paths:
        for exe in ("python.exe", "pythonw.exe"):
            keys.add(str(path.with_name(exe)).replace("\\", "#").lower())
    return keys


def is_own(key: str, own: set[str] | None = None) -> bool:
    lowered = key.lower()
    if lowered in (own if own is not None else own_keys()):
        return True
    return "pythonsoftwarefoundation.python" in lowered


def is_call_app(key: str) -> bool:
    return _app_id(key) not in NOT_CALLS


def read_mic_usage() -> list[MicUse]:
    """Apps Windows currently lists as using the microphone."""
    if sys.platform != "win32":
        return []
    import winreg

    found = []

    def scan(path: str, nested: bool):
        try:
            parent = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with parent:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(parent, i)
                except OSError:
                    break
                i += 1
                if nested and name == "NonPackaged":
                    continue
                try:
                    with winreg.OpenKey(parent, name) as key:
                        start = winreg.QueryValueEx(key, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(key, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if start and stop == 0:
                    found.append(MicUse(name, int(start)))

    scan(CONSENT_KEY, nested=True)
    scan(CONSENT_KEY + r"\NonPackaged", nested=False)
    return found


class MicWatcher:
    """Reports each call app that has started using the mic since the last poll."""

    def __init__(self, ignored: set[str] | None = None,
                 reader: Callable[[], list[MicUse]] = read_mic_usage,
                 now: int | None = None, own: set[str] | None = None):
        self.reader = reader
        self.ignored = {name.lower() for name in ignored or set()}
        self.own = own if own is not None else own_keys()
        now = filetime_now() if now is None else now
        self.since = now - STARTUP_GRACE_SECONDS * 10_000_000
        self.seen: set[MicUse] = set()
        self.active: list[MicUse] = []

    def poll(self) -> list[MicUse]:
        try:
            current = self.reader()
        except Exception:  # noqa: BLE001
            return []
        self.active = [u for u in current if not is_own(u.key, self.own) and is_call_app(u.key)]
        new = []
        for use in self.active:
            if use in self.seen:
                continue
            self.seen.add(use)
            if use.start >= self.since and use.app.lower() not in self.ignored:
                new.append(use)
        return new

    def still_using(self, use: MicUse) -> bool:
        return use in self.active


def parse_ignored(value: str) -> set[str]:
    return {name.strip() for name in re.split(r"[,;]", value or "") if name.strip()}
