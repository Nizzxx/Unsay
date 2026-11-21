"""
devices.py - the two things Unsay creates, as simple start/stop classes.

    VirtualMicrophone : real mic -> safety buffer (delay + unsay) -> BlackHole / VB-Cable
    VirtualCamera     : real webcam -> same delay, synced to the mic -> OBS Virtual Camera

The camera follows the microphone's timeline, so create the mic first:

    mic = VirtualMicrophone(out_name="BlackHole", delay=2.0)
    cam = VirtualCamera(mic, camera=0)
    mic.start(); cam.start(); mic.start_hotkeys("ctrl+shift+z")
    mic.unsay_press(); mic.unsay_release()      # hold-to-hide (GUI button)
    mic.set_delay(1.5); mic.stats(); cam.preview
    cam.stop(); mic.stop()
"""

import os
import subprocess
import sys
import threading

from modules.audio_engine import UnsayEngine, parse_combo

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.video_engine import VideoSync


# --------------------------------------------------------------------------- #
#  Device discovery (for drop-down menus)
# --------------------------------------------------------------------------- #
VIRTUAL_OUT_HINTS = ("blackhole", "cable input", "vb-audio")


def list_microphones():
    """[(index, name)] of real input devices (virtual cables excluded)."""
    import sounddevice as sd
    out = []
    for i, d in enumerate(sd.query_devices()):
        name = d["name"]
        if d["max_input_channels"] > 0 and not any(h in name.lower() for h in ("blackhole", "cable output")):
            out.append((i, name))
    return out


def list_virtual_outputs():
    """[(index, name)] of output devices, virtual cables first."""
    import sounddevice as sd
    devs = [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]
    devs.sort(key=lambda x: 0 if any(h in x[1].lower() for h in VIRTUAL_OUT_HINTS) else 1)
    return devs


def default_virtual_output():
    """Name of the virtual cable to send audio to, or None if not installed."""
    for _, name in list_virtual_outputs():
        if any(h in name.lower() for h in VIRTUAL_OUT_HINTS):
            return name
    return None


def list_cameras(max_index=6):
    """Indices of cameras that deliver frames. Call from the main thread (macOS permissions)."""
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                found.append(i)
        cap.release()
    return found


# --------------------------------------------------------------------------- #
#  Virtual microphone
# --------------------------------------------------------------------------- #
class VirtualMicrophone:
    """Real mic -> fixed-delay safety buffer with UNSAY -> virtual audio cable."""

    def __init__(self, out_name, in_name=None, delay=2.0):
        self.out_name = out_name
        self.in_name = in_name
        self.delay = float(delay)
        self.engine = None
        self.running = False
        self.devices = (None, None)
        self._hk_proc = None
        self.hotkey_error = None

    @property
    def core(self):
        return self.engine.core if self.engine else None

    def start(self):
        """Start capturing. Returns (mic name, virtual output name)."""
        if self.running:
            return self.devices
        d = self.delay
        self.engine = UnsayEngine(out_name=self.out_name, in_name=self.in_name,
                                  min_delay=d, max_delay=d, safe_delay=max(d + 1.0, 2.0))
        self.devices = self.engine.start()
        self.running = True
        return self.devices

    def stop(self):
        self.stop_hotkeys()
        if self.engine:
            self.engine.stop()
        self.running = False

    # hotkeys -----------------------------------------------------------------
    def start_hotkeys(self, undo_key="ctrl+shift+z", on_event=None, separate_process=True):
        """Global keys that work while Zoom/Discord is in front.
        separate_process=True (used by the desktop app): the keyboard listener runs in a
        helper process, because pynput + Tkinter in one process crashes on macOS."""
        parse_combo(undo_key)           # raises ValueError if invalid
        self.stop_hotkeys()
        self.hotkey_error = None
        if not separate_process:
            self.engine.start_hotkeys(undo_key=undo_key, on_event=on_event)
            return
        self._hk_proc = subprocess.Popen(
            self._hotkey_command(undo_key), cwd=PROJECT_DIR, text=True, bufsize=1,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read_hotkeys, args=(self._hk_proc, on_event), daemon=True).start()

    def _hotkey_command(self, undo_key):
        return [sys.executable, "-u", "-m", "modules.hotkey_process", undo_key]

    def _read_hotkeys(self, proc, on_event):
        ready = False
        for line in proc.stdout:
            cmd = line.strip()
            if not self.running:
                continue
            if cmd == "ready":
                ready = True
            elif cmd == "press":
                self.unsay_press()
            elif cmd == "release":
                self.unsay_release()
            elif cmd == "safe":
                s = self.stats()
                self.set_safe_mode(not (s and s["safe_mode"]))
            elif cmd.startswith("delay "):
                try:
                    self.set_delay(self.engine.core.delay_setting() + float(cmd.split()[1]))
                except (ValueError, IndexError):
                    pass
            if on_event and cmd and cmd != "ready":
                try:
                    on_event(cmd)
                except Exception:
                    pass
        # helper process ended
        if proc is self._hk_proc and self.running:
            err = proc.stderr.read().strip().splitlines() if proc.stderr else []
            self.hotkey_error = (err[-1] if err else "keyboard helper stopped") if not ready or err \
                else "keyboard helper stopped"

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
        if self.engine and self.engine._hotkeys is not None:
            try:
                self.engine._hotkeys.stop()
            except Exception:
                pass
            self.engine._hotkeys = None

    # controls (safe from any thread) -----------------------------------------
    def unsay(self):
        return self.engine.undo() if self.running else 0.0

    def unsay_press(self):
        return self.engine.unsay_press() if self.running else 0.0

    def unsay_release(self):
        if self.running:
            self.engine.unsay_release()

    def set_delay(self, seconds):
        self.delay = float(seconds)
        if self.running:
            return self.engine.set_delay(seconds)
        return self.delay

    def set_safe_mode(self, on):
        return self.engine.set_safe_mode(on) if self.running else False

    def stats(self):
        if not self.running:
            return None
        return self.engine.get_stats()


# --------------------------------------------------------------------------- #
#  Virtual camera
# --------------------------------------------------------------------------- #
class VirtualCamera:
    """Real webcam -> virtual camera, following the microphone's timeline."""

    def __init__(self, mic: VirtualMicrophone, camera=0, width=1280, height=720, fps=30, badge=True):
        self.mic = mic
        self.camera = int(camera)
        self.width, self.height, self.fps = width, height, fps
        self.badge = badge
        self.sync = None

    @property
    def running(self):
        return bool(self.sync and self.sync.running)

    @property
    def error(self):
        return self.sync.error if self.sync else None

    @property
    def preview(self):
        """Latest frame sent to the virtual camera (BGR numpy array) or None."""
        return self.sync.preview if self.sync else None

    @property
    def frozen(self):
        core = self.mic.core
        return bool(core and core.video_target()[1])

    def start(self):
        """Start the camera. Must be called from the main thread (macOS camera permission).
        Returns None if OK, otherwise an error message."""
        if not self.mic.running:
            return "Start the microphone first (the camera follows its timeline)."
        self.sync = VideoSync(self.mic.core, camera=self.camera, width=self.width,
                              height=self.height, fps=self.fps, badge=self.badge)
        self.sync.start()
        return self.sync.error

    def stop(self):
        if self.sync:
            self.sync.stop()
