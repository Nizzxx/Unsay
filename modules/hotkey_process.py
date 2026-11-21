"""
hotkey_process.py - global UNSAY hotkeys in a SEPARATE process.

On macOS, running pynput's keyboard listener in the same process as a Tkinter
window crashes Python ("trace trap"). So the desktop app starts this tiny helper
process instead; it listens to the keyboard and prints one line per event:

    press        UNSAY key went down
    release      UNSAY key went up
    safe         Ctrl+Shift+S
    delay +0.5   Ctrl+Shift+Up      (delay -0.5 for Down)

Run by modules/devices.py as:  python -m modules.hotkey_process "ctrl+shift+z"
It exits on its own when the app closes (its stdin is closed).
"""

import sys
import threading
import types

from modules.audio_engine import UnsayEngine


def emit(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


class _Relay:
    """Stands in for the audio engine: instead of acting, it reports to the app."""

    def __init__(self):
        self._hotkeys = None
        self.core = types.SimpleNamespace(delay_setting=lambda: 0.0)

    def unsay_press(self):
        emit("press")
        return 0.0

    def unsay_release(self):
        emit("release")

    def toggle_safe_mode(self):
        emit("safe")

    def set_delay(self, change):
        emit(f"delay {change:+.1f}")
        return change


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "ctrl+shift+z"
    relay = _Relay()
    UnsayEngine.start_hotkeys(relay, undo_key=key)     # same key logic as the engine
    emit("ready")
    # Block until the app closes our stdin (or dies), then exit.
    done = threading.Event()

    def watch_stdin():
        try:
            while sys.stdin.readline():
                pass
        finally:
            done.set()

    threading.Thread(target=watch_stdin, daemon=True).start()
    done.wait()


if __name__ == "__main__":
    main()
