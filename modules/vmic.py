"""
vmic.py - the Unsay virtual microphone, in the same style as vcam.py.

    real mic -> safety buffer (delay + UNSAY) -> virtual cable (BlackHole / VB-Cable)
    Zoom / Discord then pick "BlackHole 2ch" (Mac) or "CABLE Output" (Windows) as their mic.

Uses the same settings profile as the camera, so both share one delay:
    settings[name]["video"]["delay"]        -> delay in seconds (shared with the camera)
    settings[name]["audio"]["audio_in"]     -> real mic name (None = system default)
    settings[name]["audio"]["audio_out"]    -> virtual cable name (None = auto-detect)
    settings[name]["controls"]["undo_key"]  -> global UNSAY key (default "ctrl+shift+z")

Usage (e.g. from app.py):
    mic = VirtualMicrophone("Cam1")
    mic.start()                    # returns right away (audio runs on its own threads)
    mic.unsay_press(); mic.unsay_release()   # hold-to-hide (bind to a button)
    mic.set_delay(1.5); mic.stats()
    mic.stop()
"""

import os
import subprocess
import sys
import threading

from modules import settings_manager
from modules.audio_engine import UnsayEngine, parse_combo

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIRTUAL_OUT_HINTS = ("blackhole", "cable input", "vb-audio")
DEFAULT_UNDO_KEY = "ctrl+shift+z"


def find_virtual_output():
    """Name of the installed virtual cable (BlackHole / VB-Cable), or None."""
    import sounddevice as sd
    for d in sd.query_devices():
        if d["max_output_channels"] > 0 and any(h in d["name"].lower() for h in VIRTUAL_OUT_HINTS):
            return d["name"]
    return None


class VirtualMicrophone:

    def __init__(self, cam_name: str):
        self.cam_name = cam_name
        self.settings = settings_manager.get_settings(self.cam_name)
        audio = self.settings.setdefault('audio', {})
        controls = self.settings.setdefault('controls', {})

        self.delay = float(self.settings['video']['delay'])      # shared with the camera
        self.audio_in = audio.get('audio_in')                    # None = system default mic
        self.audio_out = audio.get('audio_out')                  # None = auto-detect the cable
        self.rate = audio.get('audio_rate', 48000)
        self.undo_key = controls.get('undo_key') or DEFAULT_UNDO_KEY

        self.engine = None
        self.running = False
        self.devices = (None, None)
        self.hotkey_error = None
        self._hk_proc = None

    # ------------------------------------------------------------ start/stop --
    def start(self, hotkeys=True):
        """Start the virtual mic. Returns (mic name, virtual cable name). Non-blocking."""
        if self.running:
            return self.devices
        out = self.audio_out or find_virtual_output()
        if not out:
            raise RuntimeError("No virtual cable found. Install BlackHole 2ch (Mac) or VB-Cable (Windows).")
        d = self.delay
        self.engine = UnsayEngine(out_name=out, in_name=self.audio_in, rate=self.rate,
                                  min_delay=d, max_delay=d, safe_delay=max(d + 1.0, 2.0))
        self.devices = self.engine.start()
        self.running = True
        if hotkeys:
            self.start_hotkeys(self.undo_key)
        return self.devices

    def stop(self):
        self.stop_hotkeys()
        if self.engine:
            self.engine.stop()
        self.running = False

    # --------------------------------------------------------------- unsay ----
    def unsay(self):
        """Tap: the last <delay> seconds are never heard."""
        return self.engine.undo() if self.running else 0.0

    def unsay_press(self):
        """Button/key DOWN: unsay, and stay silent while held."""
        return self.engine.unsay_press() if self.running else 0.0

    def unsay_release(self):
        """Button/key UP: back live."""
        if self.running:
            self.engine.unsay_release()

    def set_delay(self, seconds):
        self.delay = float(seconds)
        if self.running:
            self.engine.set_delay(self.delay)
        return self.delay

    def stats(self):
        """dict with delay, speaking, holding, undo_count... (None when stopped)."""
        return self.engine.get_stats() if self.running else None

    # ------------------------------------------------------------- hotkeys ----
    def start_hotkeys(self, undo_key=None):
        """Global UNSAY key, works while Zoom/Discord is in front.
        Runs in a helper process: pynput + Tkinter in one process crashes on macOS."""
        undo_key = undo_key or self.undo_key
        parse_combo(undo_key)                      # raises ValueError if invalid
        self.stop_hotkeys()
        self.undo_key = undo_key
        self.hotkey_error = None
        self._hk_proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "modules.hotkey_process", undo_key],
            cwd=PROJECT_DIR, text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read_hotkeys, args=(self._hk_proc,), daemon=True).start()

    def _read_hotkeys(self, proc):
        for line in proc.stdout:
            cmd = line.strip()
            if not self.running:
                continue
            if cmd == "press":
                self.unsay_press()
            elif cmd == "release":
                self.unsay_release()
            elif cmd == "safe":
                s = self.stats()
                self.engine.set_safe_mode(not (s and s["safe_mode"]))
            elif cmd.startswith("delay "):
                try:
                    self.set_delay(self.engine.core.delay_setting() + float(cmd.split()[1]))
                except (ValueError, IndexError):
                    pass
        if proc is self._hk_proc and self.running:
            err = proc.stderr.read().strip().splitlines() if proc.stderr else []
            self.hotkey_error = err[-1] if err else "keyboard helper stopped"

    def stop_hotkeys(self):
        if self._hk_proc is not None:
            proc, self._hk_proc = self._hk_proc, None
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # ------------------------------------------------------------ settings ----
    def modify_settings(self):
        self.settings['video']['delay'] = self.delay
        self.settings['audio']['audio_in'] = self.audio_in
        self.settings['audio']['audio_out'] = self.audio_out
        self.settings['controls']['undo_key'] = self.undo_key

        settings_manager.save_settings(self.settings, self.cam_name)
