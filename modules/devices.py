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
VIRTUAL_OUT_HINTS = ("blackhole", "cable input", "cable in ", "vb-audio")
VIRTUAL_IN_HINTS = ("blackhole", "cable output", "cable out ", "obs virtual")


def _usable_devices():
    """Windows lists each device 3-4 times (MME/DirectSound/WASAPI/WDM-KS) and some
    copies refuse 48 kHz: keep one copy (DirectSound, else MME)."""
    import sounddevice as sd
    devs = list(enumerate(sd.query_devices()))
    if sys.platform != "win32":
        return devs
    apis = sd.query_hostapis()
    for wanted in ("Windows DirectSound", "MME"):
        ids = [i for i, a in enumerate(apis) if a["name"] == wanted]
        if ids:
            keep = [(i, d) for i, d in devs if d["hostapi"] == ids[0]]
            if keep:
                return keep
    return devs


def list_microphones():
    """[(index, name)] of real input devices (virtual cables excluded)."""
    out, seen = [], set()
    for i, d in _usable_devices():
        n = d["name"]
        if d["max_input_channels"] > 0 and n not in seen \
                and not any(h in n.lower() for h in VIRTUAL_IN_HINTS) \
                and "sound mapper" not in n.lower() and "primary sound capture" not in n.lower():
            seen.add(n)
            out.append((i, n))
    return out


def list_virtual_outputs():
    """[(index, name)] of VIRTUAL cables only. Real speakers/AirPods are never offered:
    audio sent there plays into your own ears and Zoom/Discord hear nothing."""
    out, seen = [], set()
    for i, d in _usable_devices():
        n = d["name"]
        if d["max_output_channels"] > 0 and any(h in n.lower() for h in VIRTUAL_OUT_HINTS) and n not in seen:
            seen.add(n)
            out.append((i, n))
    return out


def default_virtual_output():
    """Name of the virtual cable to send audio to, or None if not installed."""
    outs = list_virtual_outputs()
    return outs[0][1] if outs else None


def auto_microphone():
    """The mic Unsay listens to. None = the system default mic (the normal case:
    whatever the user already talks into). If the system default is itself a virtual
    cable, Unsay would hear its own output, so take the first real mic instead."""
    import sounddevice as sd
    try:
        default = sd.query_devices(kind="input")["name"]
    except Exception:
        default = ""
    if default and not any(h in default.lower() for h in VIRTUAL_IN_HINTS):
        return None
    mics = list_microphones()
    return mics[0][1] if mics else None


def microphone_label(name):
    """Readable name for the status line."""
    if name:
        return name
    import sounddevice as sd
    try:
        n = sd.query_devices(kind="input")["name"]
    except Exception:
        return "your default mic"
    return "your default mic" if ("mapper" in n.lower() or "primary" in n.lower()) else n


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
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))   # Windows: no extra console
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
                proc.wait(timeout=0.5)
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
