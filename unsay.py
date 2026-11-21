"""
unsay.py - command-line version of Unsay (the desktop app is app.py).
One tool that gives every app a new virtual MIC and a new virtual CAMERA,
both passing through Unsay's safety buffer.

    real mic    -> adaptive delay + undo -> virtual mic    (BlackHole / VB-Cable)
    real webcam -> follows the audio     -> virtual camera (OBS virtual camera driver)

The video always shows exactly the moment you hear (lips stay in sync while the
delay grows and shrinks). When you UNSAY, the video freezes on the last frame
before the deleted sentence, so nobody sees you mouthing words with no sound.

One-time setup
    pip install sounddevice numpy pynput opencv-python pyvirtualcam
    macOS  : install BlackHole 2ch, and OBS Studio (open it once, click
             "Start Virtual Camera", then "Stop" - this installs the driver).
             Allow your terminal in Settings > Privacy & Security >
             Camera, Microphone and Accessibility (for the hotkeys).
    Windows: install VB-Cable and OBS Studio.
    Do NOT run OBS's own virtual camera while Unsay is running.

Run
    python unsay.py --list-audio
    python unsay.py --audio-out "BlackHole"            # mic + camera, fixed 2 s delay
    python unsay.py --audio-out "BlackHole" --delay 3  # fixed 3 s delay
    python unsay.py --audio-out "BlackHole" --mode adaptive
    python unsay.py --audio-out "BlackHole" --delay 1.5 --undo-key "f9"
    python unsay.py --audio-out "BlackHole" --no-video # mic only
    Then in Zoom/Discord/Meet choose:
        microphone: "BlackHole 2ch" (Mac) / "CABLE Output" (Windows)
        camera    : "OBS Virtual Camera"

    Ctrl+Shift+Z = UNSAY    Ctrl+Shift+S = Safe Mode    Ctrl+C = quit

From the GUI
    from unsay import Unsay
    u = Unsay(audio_out="BlackHole")
    u.start()                 # starts mic, camera and hotkeys
    u.undo(); u.toggle_safe_mode(); u.set_delay(3.0); u.get_stats(); u.stop()
"""

import argparse
import bisect
import sys
import threading
import time
from collections import deque

import numpy as np

from modules.audio_engine import UnsayEngine, list_audio_devices
from modules.video_engine import VideoSync, list_cameras


class Unsay:
    """The whole tool: virtual mic + virtual camera + hotkeys. Thread-safe controls."""

    def __init__(self, audio_out, audio_in=None, video=True, camera=0,
                 width=1280, height=720, fps=30, badge=True,
                 mode="fixed", delay=2.0, **core_kwargs):
        # mode "fixed"   : constant delay (protects video too, even when you are silent)
        # mode "adaptive": 0.5-1.5 s, grows while you talk, shrinks when you stop
        self.mode = mode
        if mode == "fixed":
            core_kwargs.setdefault("min_delay", delay)
            core_kwargs.setdefault("max_delay", delay)
            core_kwargs.setdefault("safe_delay", max(delay + 1.0, 2.0))
        self.audio = UnsayEngine(out_name=audio_out, in_name=audio_in, **core_kwargs)
        self.video = (VideoSync(self.audio.core, camera=camera, width=width, height=height,
                                fps=fps, badge=badge) if video else None)
        self.devices = None

    def start(self, hotkeys=True, on_event=None, undo_key="ctrl+shift+z"):
        self.devices = self.audio.start()
        if self.video:
            self.video.start()
        if hotkeys:
            self.audio.start_hotkeys(undo_key=undo_key, on_event=on_event)
        return self.devices

    def stop(self):
        if self.video:
            self.video.stop()
        self.audio.stop()

    def undo(self):
        return self.audio.undo()

    def unsay_press(self):
        """GUI button pressed: unsay, and stay hidden while held."""
        return self.audio.unsay_press()

    def unsay_release(self):
        """GUI button released: back live."""
        return self.audio.unsay_release()

    def toggle_safe_mode(self):
        return self.audio.toggle_safe_mode()

    def set_safe_mode(self, on):
        return self.audio.set_safe_mode(on)

    def set_delay(self, seconds):
        """Live delay change, e.g. from a slider: u.set_delay(3.0)"""
        return self.audio.set_delay(seconds)

    def get_stats(self):
        s = self.audio.get_stats()
        s["video_status"] = self.video.status if self.video else "off"
        s["video_error"] = self.video.error if self.video else None
        s["video_frozen"] = self.audio.core.video_target()[1]
        return s


def main():
    p = argparse.ArgumentParser(description="Unsay: virtual mic + camera with an undo button")
    p.add_argument("--audio-out", help="virtual cable: 'BlackHole' (Mac) or 'CABLE Input' (Windows)")
    p.add_argument("--audio-in", default=None, help="real microphone (default: system mic)")
    p.add_argument("--mode", choices=["fixed", "adaptive"], default="fixed",
                   help="fixed = constant delay (default), adaptive = grows/shrinks with speech")
    p.add_argument("--delay", type=float, default=None, help="delay in seconds for fixed mode")
    p.add_argument("--undo-key", default=None,
                   help='key for UNSAY, e.g. "ctrl+shift+z", "f9", "cmd+u", "alt+space"')
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--no-video", action="store_true")
    p.add_argument("--no-badge", action="store_true")
    p.add_argument("--list-audio", action="store_true")
    p.add_argument("--list-cameras", action="store_true")
    a = p.parse_args()

    # Pressing Ctrl+Shift+Z while the terminal is focused sends Ctrl+Z, which
    # would SUSPEND this program (audio and video stop). Ignore that signal.
    import signal
    if hasattr(signal, "SIGTSTP"):
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)

    if a.list_audio:
        list_audio_devices()
        return
    if a.list_cameras:
        list_cameras()
        return
    if not a.audio_out:
        sys.exit("Give --audio-out (see --list-audio).")

    # Ask the user for the delay and the UNSAY key (Enter = default)
    from modules.audio_engine import parse_combo as _pc
    if a.delay is None:
        ans = input("Delay in seconds (longer = more time to unsay, but longer freeze) [2]: ").strip()
        try:
            a.delay = float(ans) if ans else 2.0
        except ValueError:
            a.delay = 2.0
        a.delay = min(max(a.delay, 0.3), 10.0)
    if a.undo_key is None:
        while True:
            ans = input("UNSAY key, e.g. ctrl+shift+z, f9, cmd+u [ctrl+shift+z]: ").strip()
            a.undo_key = ans or "ctrl+shift+z"
            try:
                _pc(a.undo_key)
                break
            except ValueError as e:
                print(f"  {e}")

    u = Unsay(audio_out=a.audio_out, audio_in=a.audio_in, video=not a.no_video,
              camera=a.camera, width=a.width, height=a.height, fps=a.fps,
              badge=not a.no_badge, mode=a.mode, delay=a.delay)
    def show(name, result):
        msg = {"undo": f"UNSAY - removed {result:.2f}s",
               "safe": f"SAFE MODE {'ON' if result else 'OFF'}",
               "delay": f"DELAY set to {result:.1f}s",
               "release": "back live"}.get(name, name)
        print(f"\n>> {msg}")

    from modules.audio_engine import parse_combo
    try:
        parse_combo(a.undo_key)
    except ValueError as e:
        sys.exit(str(e))
    mic_in, mic_out = u.start(on_event=show, undo_key=a.undo_key)
    print(f"Mic   : '{mic_in}' -> '{mic_out}'")
    print(f"Mode  : {a.mode}" + (f" ({a.delay:.1f}s)" if a.mode == "fixed" else " (0.5-1.5s)"))
    if u.video:
        time.sleep(2)
        if u.video.error:
            print(f"Video : FAILED - {u.video.error}")
        else:
            print(f"Video : camera {a.camera} -> '{u.video.virtual_name}'")
    print(f"{a.undo_key.upper()} = UNSAY (tap = unsay, hold = stay hidden until you let go)")
    print("Ctrl+Shift+S = Safe Mode   Ctrl+C = quit")
    print("Ctrl+Shift+Up / Down = delay +/- 0.5 s")
    shown_error = None
    try:
        while True:
            s = u.get_stats()
            if s["video_error"] and s["video_error"] != shown_error:
                shown_error = s["video_error"]
                print(f"\n>> VIDEO STOPPED: {shown_error}")
            mode = "SAFE" if s["safe_mode"] else "auto"
            talk = "talking" if s["speaking"] else "quiet  "
            frz = "  HIDDEN (holding)" if s["holding"] else ("  VIDEO FROZEN    " if s["video_frozen"] else "                  ")
            print(f"\rdelay {s['delay']:.2f}s (set {s['delay_setting']:.1f}s)  {mode}  {talk}  "
                  f"undos {s['undo_count']}{frz}", end="")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        u.stop()


if __name__ == "__main__":
    main()
