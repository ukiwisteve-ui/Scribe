"""Command line entry point.

    python -m scribe                  open the Scribe window
    python -m scribe devices          list audio devices
    python -m scribe record [notes]   record from the terminal (Ctrl+C to stop)
    python -m scribe process FOLDER   (re)process a recorded session
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from . import audio, pipeline
from .config import Settings


def cmd_devices(_args):
    outputs, mics, default_out, default_mic = audio.list_devices()
    print("Call audio (outputs):")
    for d in outputs:
        print(f"  [{d.index}] {d.name}{'  (default)' if d is default_out else ''}")
    print("Microphones:")
    for d in mics:
        print(f"  [{d.index}] {d.name}{'  (default)' if d is default_mic else ''}")


def cmd_record(args):
    settings = Settings.load()
    outputs, mics, default_out, default_mic = audio.list_devices()
    output = next((d for d in outputs if d.index == args.output), None) if args.output is not None else default_out
    mic = None
    if not args.no_mic:
        mic = next((d for d in mics if d.index == args.mic), None) if args.mic is not None else default_mic
    if output is None:
        sys.exit("No output device found to record from.")
    context = " ".join(args.notes)
    folder = pipeline.new_session_dir(settings, context)
    meta = pipeline.SessionMeta(started=datetime.now().strftime("%Y-%m-%d %H:%M"), context=context)
    recorder = audio.Recorder(folder, output, mic)
    recorder.start()
    print(f"Recording '{output.name}'" + (f" and '{mic.name}'" if mic else "") + ". Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            e = int(recorder.elapsed())
            print(f"\r{e // 3600:02d}:{e % 3600 // 60:02d}:{e % 60:02d}", end="", flush=True)
    except KeyboardInterrupt:
        print()
    meta.tracks = pipeline.tracks_to_meta(recorder.stop())
    meta.save(folder)
    pipeline.process_session(folder, settings)


def cmd_process(args):
    pipeline.process_session(Path(args.folder), Settings.load())


def main():
    parser = argparse.ArgumentParser(prog="scribe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("devices", help="list audio devices").set_defaults(func=cmd_devices)
    rec = sub.add_parser("record", help="record from the terminal")
    rec.add_argument("notes", nargs="*", help="what the call is about (optional)")
    rec.add_argument("--output", type=int, help="output device index (see 'devices')")
    rec.add_argument("--mic", type=int, help="microphone device index (see 'devices')")
    rec.add_argument("--no-mic", action="store_true", help="don't record the microphone")
    rec.set_defaults(func=cmd_record)
    proc = sub.add_parser("process", help="(re)process a recorded session folder")
    proc.add_argument("folder")
    proc.set_defaults(func=cmd_process)

    args = parser.parse_args()
    if args.command is None:
        from .gui import main as gui_main
        gui_main()
    else:
        args.func(args)


if __name__ == "__main__":
    main()
