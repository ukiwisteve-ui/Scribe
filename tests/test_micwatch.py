import sys

import pytest

from scribe import micwatch
from scribe.micwatch import MicUse

NOW = 134_369_700_000_000_000
MINUTE = 60 * 10_000_000
OWN = {r"c:#python313#python.exe", r"c:#python313#pythonw.exe"}


def watcher(uses, ignored=None):
    current = list(uses)
    w = micwatch.MicWatcher(ignored=ignored, reader=lambda: current, now=NOW, own=OWN)
    return w, current


def test_app_names():
    assert micwatch.app_name("MSTeams_8wekyb3d8bbwe") == "Teams"
    assert micwatch.app_name("5319275A.WhatsAppDesktop_cv1g1gvanyjgm") == "WhatsApp"
    assert micwatch.app_name(r"C:#Users#Steve#AppData#Roaming#Zoom#bin#Zoom.exe") == "Zoom"
    assert micwatch.app_name(r"C:#Program Files#Google#Chrome#Application#chrome.exe") == "Chrome"
    assert micwatch.app_name(r"C:#Tools#Odd App#oddapp.exe") == "oddapp"
    assert micwatch.app_name("Contoso.Caller_abc123") == "Caller"


def test_new_call_is_reported_once():
    w, current = watcher([])
    assert w.poll() == []
    teams = MicUse("MSTeams_8wekyb3d8bbwe", NOW + MINUTE)
    current.append(teams)
    assert [u.app for u in w.poll()] == ["Teams"]
    assert w.poll() == []
    assert w.still_using(teams)
    current.clear()
    assert w.poll() == [] and not w.still_using(teams)
    # A second call in the same app is a new start time, so it prompts again.
    current.append(MicUse("MSTeams_8wekyb3d8bbwe", NOW + 30 * MINUTE))
    assert [u.app for u in w.poll()] == ["Teams"]


def test_stale_and_recent_entries_at_startup():
    crashed = MicUse(r"C:#Users#Steve#AppData#Local#Discord#app-1.0.9179#Discord.exe",
                     NOW - 600 * 24 * 60 * MINUTE)
    ongoing = MicUse(r"C:#Users#Steve#AppData#Roaming#Zoom#bin#Zoom.exe", NOW - 5 * MINUTE)
    w, _ = watcher([crashed, ongoing])
    assert [u.app for u in w.poll()] == ["Zoom"]


def test_scribe_itself_other_tools_and_ignored_apps_are_skipped():
    w, current = watcher([], ignored={"Discord"})
    current += [
        MicUse(r"C:#Python313#python.exe", NOW + MINUTE),
        MicUse("PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0", NOW + MINUTE),
        MicUse("Microsoft.WindowsSoundRecorder_8wekyb3d8bbwe", NOW + MINUTE),
        MicUse(r"C:#Program Files (x86)#iMyFone MagicMic#x86#MFAudioLocal.exe", NOW + MINUTE),
        MicUse(r"C:#Users#Steve#AppData#Local#Discord#app-1.0.9262#Discord.exe", NOW + MINUTE),
        MicUse(r"C:#Program Files#Slack#slack.exe", NOW + MINUTE),
    ]
    assert [u.app for u in w.poll()] == ["Slack"]


def test_reader_errors_are_ignored():
    def broken():
        raise OSError("registry unavailable")

    w = micwatch.MicWatcher(reader=broken, now=NOW, own=OWN)
    assert w.poll() == []


def test_parse_ignored():
    assert micwatch.parse_ignored(" Discord, Chrome;;Zoom ") == {"Discord", "Chrome", "Zoom"}
    assert micwatch.parse_ignored("") == set()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_reads_the_real_registry_and_knows_its_own_python():
    uses = micwatch.read_mic_usage()
    assert all(u.start > 0 for u in uses)
    own = micwatch.own_keys()
    assert str(sys.executable).replace("\\", "#").lower() in own
