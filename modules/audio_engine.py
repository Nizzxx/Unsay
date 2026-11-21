"""
audio_engine.py - the Unsay microphone engine.

Real mic -> safety buffer (adaptive delay + undo) -> virtual audio cable.
Zoom / Discord / Meet then use the virtual cable as their microphone.

    Windows : install VB-Cable, out device "CABLE Input",  apps use "CABLE Output"
    macOS   : install BlackHole 2ch, out device "BlackHole", apps use "BlackHole 2ch"

Install:
    pip install sounddevice numpy pynput

Run on its own (no GUI) to test:
    python -m modules.audio_engine --list-audio
    python -m modules.audio_engine --audio-out "BlackHole"
    python -m modules.audio_engine --audio-out "CABLE Input"

    Ctrl+Shift+Z  = UNSAY (delete what is still in the buffer)
    Ctrl+Shift+S  = toggle Safe Mode (fixed long delay)
    Ctrl+C        = quit

Use from the Tkinter GUI:
    from modules.audio_engine import UnsayEngine
    engine = UnsayEngine(out_name="BlackHole")
    engine.start()
    engine.start_hotkeys()            # optional global hotkeys
    button.configure(command=engine.undo)
    # every ~100 ms in root.after(...):
    stats = engine.get_stats()        # dict, safe to call from the GUI thread
    engine.stop()

How the delay works (all in DelayCore, no audio hardware needed):
  * Audio is cut into 20 ms frames. Each frame is labelled speech or silence.
  * The delay is simply how many frames are waiting in the queue.
  * GROW  : while you are talking, every 2nd frame of a short pause is played
            twice, so pauses get a bit longer and the queue grows.
  * SHRINK: once you have been quiet for 0.7 s, silent frames at the front of
            the queue are thrown away (several per tick), so the delay
            drops back to the minimum before you speak again.
  * UNDO  : deletes everything in the queue back to the start of the current
            sentence (the last real pause) and replaces it with silence. Speech
            that arrives right after the press (the end of the word you were
            saying) is also muted until your next pause.
  * SAFE MODE: holds the delay at a fixed, longer value for risky moments.
"""

import argparse
import sys
import threading
import time
from collections import deque

import numpy as np


# --------------------------------------------------------------------------- #
#  Core logic: pure numpy, no audio devices, so it can be tested anywhere.
# --------------------------------------------------------------------------- #
class DelayCore:
    def __init__(
        self,
        rate=48000,
        frame_ms=20,
        min_delay=0.5,       # never less than this: the guaranteed undo window
        max_delay=1.5,       # adaptive delay grows up to this while talking
        safe_delay=2.0,      # Safe Mode fixed delay
        quiet_to_shrink=0.7, # seconds of silence before the delay shrinks
        stretch_every=2,     # duplicate 1 out of N silent frames while talking
        drop_per_tick=3,     # max silent frames discarded per output tick
        hangover_ms=200,     # keep "speech" on this long after it stops (no chopping)
        pause_ms=150,        # a pause this long counts as a sentence boundary for undo
        max_mute=0.6,        # after UNSAY, mute at most this long (or until your next pause)
    ):
        self.rate = rate
        self.frame = int(rate * frame_ms / 1000)
        self.frame_s = frame_ms / 1000
        self.min_frames = round(min_delay / self.frame_s)
        self.max_frames = round(max_delay / self.frame_s)
        self.safe_frames = round(safe_delay / self.frame_s)
        self.quiet_frames_to_shrink = round(quiet_to_shrink / self.frame_s)
        self.stretch_every = stretch_every
        self.drop_per_tick = drop_per_tick
        self.hangover = round(hangover_ms / frame_ms)
        self.pause_frames = round(pause_ms / frame_ms)
        self.max_mute = max_mute
        self.mute_started = None
        self.holding = False        # UNSAY key held down: stay hidden until released
        self.hold_started = 0.0
        self.max_hold = 30.0        # safety: never stay hidden longer than this
        self.valve = self.max_frames + 10   # hard cap on queue length (see pull)

        self.lock = threading.Lock()
        self.silence = np.zeros(self.frame, dtype=np.float32)
        # queue entries: (samples, is_speech, capture_time)
        # capture_time = when the mic recorded it (None for filler silence).
        # The video side uses it to show exactly the frame that matches the audio.
        self.queue = deque((self.silence, False, None) for _ in range(self.min_frames))
        self.playhead = None        # capture_time of the audio being played right now
        self.freezes = []           # [start, end] capture-time ranges hidden by undo

        # VAD state
        self.noise_floor = 1e-3
        self.speech_hold = 0            # hangover counter
        self.quiet_run = 10_000         # consecutive silent input frames
        self.since_speech = 10_000      # frames since last speech (for "mid-conversation")
        self.pause_counter = 0

        # modes / stats
        self.safe_mode = False
        self.mute_until_pause = False
        self.undo_count = 0
        self.last_undo_seconds = 0.0
        self.speaking = False

    # ---------- voice activity detection ----------
    def _is_speech(self, x):
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        # Track the background noise slowly (fast down, slow up).
        if rms < self.noise_floor:
            self.noise_floor = 0.9 * self.noise_floor + 0.1 * rms
        else:
            self.noise_floor = 0.999 * self.noise_floor + 0.001 * rms
        loud = rms > max(self.noise_floor * 3.0, 0.004)
        if loud:
            self.speech_hold = self.hangover
            return True, True
        if self.speech_hold > 0:
            self.speech_hold -= 1
            return True, False   # treated as speech (no chopping), but it is quiet
        return False, False

    # ---------- input side (called for each 20 ms mic frame) ----------
    def push(self, x, t=None):
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if t is None:
            t = time.monotonic()
        with self.lock:
            speech, loud = self._is_speech(x)
            self.speaking = speech
            if speech:
                self.quiet_run = 0
            else:
                self.quiet_run += 1
            if loud:
                self.since_speech = 0
            else:
                self.since_speech += 1

            # After an undo, swallow the rest of the regretted sentence.
            if self.mute_until_pause:
                if self.mute_started is None:
                    self.mute_started = t
                held = self.holding and (time.monotonic() - self.hold_started) < self.max_hold
                if not held and ((not speech and self.quiet_run >= self.pause_frames) or
                                 t - self.mute_started >= self.max_mute):
                    self.mute_started = None
                    self.mute_until_pause = False
                    for f in self.freezes:           # close EVERY open freeze
                        if f[1] is None:
                            f[1] = t                 # video unfreezes from here
                else:
                    self.queue.append((self.silence, False, t))
                    return

            self.queue.append((x, speech, t))

            # GROW: stretch short pauses while the person is mid-conversation.
            target_max = self.safe_frames if self.safe_mode else self.max_frames
            # (quiet frames inside the hangover count too: they are pause audio)
            mid_conversation = (not loud) and self.since_speech < self.quiet_frames_to_shrink
            if mid_conversation and len(self.queue) < target_max:
                self.pause_counter += 1
                if self.pause_counter % self.stretch_every == 0:
                    self.queue.append((x, speech, t))
            if loud:
                self.pause_counter = 0

    # ---------- output side (called for each 20 ms output frame) ----------
    def pull(self):
        with self.lock:
            target_min = self.safe_frames if self.safe_mode else self.min_frames
            target_max = self.safe_frames if self.safe_mode else self.max_frames

            # SHRINK: user has been quiet a while -> skip silent frames at the front.
            dropped = 0
            if self.quiet_run >= self.quiet_frames_to_shrink:
                while (dropped < self.drop_per_tick and len(self.queue) > target_min + 1
                       and not self.queue[0][1]):
                    self.queue.popleft()
                    dropped += 1

            # Safety valve (clock drift / long monologue): never exceed max by much.
            # After the user lowers the delay, the cap follows the queue down slowly
            # (only silence gets dropped), so no words are lost.
            self.valve = max(target_max + 10, min(self.valve, len(self.queue) + 10))
            while len(self.queue) > self.valve:
                # prefer dropping silence; if the front is speech we still must drop
                self.queue.popleft()

            if not self.queue:
                # underrun (output clock faster than input): refill to the floor
                self.queue.extend((self.silence, False, None) for _ in range(target_min))
                return self.silence
            samples, _, t = self.queue.popleft()
            if t is not None:
                self.playhead = t
            return samples

    # ---------- controls ----------
    def undo(self):
        """UNSAY: everything not yet sent (the whole delay window) never happens,
        for audio AND video, plus whatever you keep saying until your next pause.
        Audio and video use the same timestamps, so they are cut identically."""
        with self.lock:
            items = list(self.queue)
            n = len(items)
            if items:
                # fade the very next frame out to avoid a click, silence the rest
                first, _, t0 = items[0]
                fade = np.linspace(1.0, 0.0, len(first), dtype=np.float32)
                items = [(first * fade, False, t0)] + [(self.silence, False, it[2]) for it in items[1:]]
            self.queue = deque(items)
            first_ts = next((it[2] for it in items if it[2] is not None), None)
            if first_ts is not None:
                self.freezes.append([first_ts, None])   # ends at the same moment audio resumes
                self.freezes = self.freezes[-20:]
            self.mute_until_pause = True
            self.undo_count += 1
            self.last_undo_seconds = n * self.frame_s
            return self.last_undo_seconds

    def hold(self, on):
        """While held, audio stays silent and video stays frozen (hold-to-hide)."""
        with self.lock:
            self.holding = bool(on)
            if on:
                self.hold_started = time.monotonic()

    def set_safe_mode(self, on):
        with self.lock:
            self.safe_mode = bool(on)
            if on:
                # jump straight to the safe delay (a short gap the user chose)
                need = self.safe_frames - len(self.queue)
                self.queue.extend((self.silence, False, None) for _ in range(max(0, need)))
        return self.safe_mode

    def toggle_safe_mode(self):
        return self.set_safe_mode(not self.safe_mode)

    def set_delay(self, seconds):
        """Set a fixed delay live (e.g. from a slider). Longer = more time to undo.
        Raising it adds a short silent gap now; lowering it happens smoothly by
        trimming silence the next time you are quiet (no words are cut)."""
        seconds = float(min(max(seconds, 0.3), 10.0))
        with self.lock:
            frames = round(seconds / self.frame_s)
            self.min_frames = self.max_frames = frames
            self.safe_frames = max(frames + round(1.0 / self.frame_s), round(2.0 / self.frame_s))
            target = self.safe_frames if self.safe_mode else frames
            need = target - len(self.queue)
            if need > 0:
                self.queue.extend((self.silence, False, None) for _ in range(need))
        return seconds

    def delay_setting(self):
        with self.lock:
            return (self.safe_frames if self.safe_mode else self.max_frames) * self.frame_s

    def video_target(self):
        """(capture_time the video should show now, frozen?) for the camera side."""
        with self.lock:
            p = self.playhead
            if p is None:
                return None, False
            for start, end in reversed(self.freezes):
                if p >= start and (end is None or p < end):
                    return start, True
            return p, False

    def delay_seconds(self):
        with self.lock:
            return len(self.queue) * self.frame_s

    def stats(self):
        with self.lock:
            return {
                "delay": len(self.queue) * self.frame_s,
                "delay_setting": (self.safe_frames if self.safe_mode else self.max_frames) * self.frame_s,
                "speaking": self.speaking,
                "safe_mode": self.safe_mode,
                "muting": self.mute_until_pause,
                "holding": self.holding,
                "undo_count": self.undo_count,
                "last_undo_seconds": self.last_undo_seconds,
            }


# --------------------------------------------------------------------------- #
#  Audio I/O: connects DelayCore to the real mic and the virtual cable.
# --------------------------------------------------------------------------- #
def find_device(sd, name, kind):
    if name is None:
        idx = sd.default.device[0 if kind == "input" else 1]
        if idx is None or idx < 0:
            raise RuntimeError(f"No default audio {kind} device.")
        return idx
    if str(name).isdigit():
        return int(name)
    key = f"max_{kind}_channels"
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d[key] > 0:
            return i
    raise RuntimeError(f'No audio {kind} device matching "{name}".')


def list_audio_devices():
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        print(f"  {i}: {d['name']}  [{d['max_input_channels']} in / {d['max_output_channels']} out]")


SPECIAL_KEYS = {"space", "enter", "esc", "tab", "up", "down", "left", "right"} | {f"f{i}" for i in range(1, 21)}
_MAC_VK = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
           "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
           "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35,
           "l": 37, "j": 38, "k": 40, "n": 45, "m": 46}


def key_vk(name):
    """Hardware key code for a letter/digit (used when the OS reports no character)."""
    if sys.platform == "darwin":
        return _MAC_VK.get(name)
    if sys.platform == "win32" and len(name) == 1 and name.isalnum():
        return ord(name.upper())
    return None


def parse_combo(text):
    """'Ctrl+Shift+Z' -> ({'ctrl','shift'}, 'z'). Accepts cmd/command, alt/option, control."""
    alias = {"control": "ctrl", "command": "cmd", "option": "alt", "opt": "alt", "return": "enter",
             "escape": "esc"}
    parts = [alias.get(p.strip().lower(), p.strip().lower()) for p in text.replace(" ", "").split("+") if p]
    mods = {p for p in parts if p in ("ctrl", "shift", "cmd", "alt")}
    keys = [p for p in parts if p not in mods]
    if len(keys) != 1:
        raise ValueError(f'Bad key combo "{text}". Examples: "ctrl+shift+z", "f9", "cmd+u".')
    key = keys[0]
    if key not in SPECIAL_KEYS and not (len(key) == 1 and key.isalnum()):
        raise ValueError(f'Unsupported key "{key}". Use a letter, a digit, f1-f20, space, enter, esc, tab or an arrow.')
    return mods, key


class UnsayEngine:
    """Real mic -> DelayCore -> virtual cable. Thread-safe controls for the GUI."""

    def __init__(self, out_name, in_name=None, rate=48000, history_seconds=30, **core_kwargs):
        self.out_name = out_name
        self.in_name = in_name
        self.rate = rate
        self.core = DelayCore(rate=rate, **core_kwargs)
        self.in_stream = None
        self.out_stream = None
        self._hotkeys = None
        self._pending = np.zeros(0, dtype=np.float32)  # input samples not yet a full frame
        # (time, delay) points for the live chart, sampled ~10x per second
        self.history = deque(maxlen=history_seconds * 10)
        self._last_hist = 0.0

    # sounddevice callbacks run on audio threads: keep them fast, never touch Tkinter
    def _on_input(self, indata, frames, time_info, status):
        x = np.concatenate([self._pending, indata[:, 0]])
        f = self.core.frame
        n = len(x) // f
        now = time.monotonic()
        for k in range(n):
            self.core.push(x[k * f:(k + 1) * f], now - (n - 1 - k) * self.core.frame_s)
        self._pending = x[n * f:].copy()

    def _on_output(self, outdata, frames, time_info, status):
        y = self.core.pull()
        if len(y) != frames:  # should not happen (blocksize == frame)
            y = np.resize(y, frames)
        outdata[:] = np.repeat(y.reshape(-1, 1), outdata.shape[1], axis=1)
        now = time.monotonic()
        if now - self._last_hist >= 0.1:
            self._last_hist = now
            self.history.append((now, self.core.delay_seconds()))

    def start(self):
        import sounddevice as sd
        in_idx = find_device(sd, self.in_name, "input")
        out_idx = find_device(sd, self.out_name, "output")
        out_ch = 2 if sd.query_devices(out_idx)["max_output_channels"] >= 2 else 1
        f = self.core.frame
        self.out_stream = sd.OutputStream(device=out_idx, samplerate=self.rate, channels=out_ch,
                                          dtype="float32", blocksize=f, callback=self._on_output)
        self.in_stream = sd.InputStream(device=in_idx, samplerate=self.rate, channels=1,
                                        dtype="float32", blocksize=f, callback=self._on_input)
        self.out_stream.start()
        self.in_stream.start()
        return (sd.query_devices(in_idx)["name"], sd.query_devices(out_idx)["name"])

    def stop(self):
        for s in (self.in_stream, self.out_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        if self._hotkeys is not None:
            self._hotkeys.stop()

    def start_hotkeys(self, undo_key="ctrl+shift+z", on_event=None):
        """Global hotkeys that work while Zoom/Discord is the active window.
        undo_key: any combo like "ctrl+shift+z", "cmd+u", "alt+space", "f9", "ctrl+1".
        Fixed extras: Ctrl+Shift+S = Safe Mode, Ctrl+Shift+Up/Down = delay +/- 0.5 s.
        macOS: allow Terminal in Privacy & Security > Accessibility AND Input Monitoring."""
        from pynput import keyboard
        K = keyboard.Key
        self.undo_key = undo_key
        want_mods, want_key = parse_combo(undo_key)
        held = set()
        mod_of = {}
        for name, keys in {"ctrl": ("ctrl", "ctrl_l", "ctrl_r"), "shift": ("shift", "shift_l", "shift_r"),
                           "cmd": ("cmd", "cmd_l", "cmd_r"), "alt": ("alt", "alt_l", "alt_r", "alt_gr")}.items():
            for k in keys:
                if hasattr(K, k):
                    mod_of[getattr(K, k)] = name

        def is_key(key, name):
            if name in SPECIAL_KEYS:
                return hasattr(K, name) and key == getattr(K, name)
            ch = getattr(key, "char", None)
            if ch:
                if ch.lower() == name:
                    return True
                if len(name) == 1 and name.isalpha() and len(ch) == 1 and ord(ch) == ord(name) - 96:
                    return True          # Ctrl+letter arrives as a control character
            vk = getattr(key, "vk", None)
            return vk is not None and vk == key_vk(name)

        state = {"undo_down": False}

        def emit(name, result):
            if on_event:
                try:
                    on_event(name, result)
                except Exception:
                    pass

        def on_press(key):
            if key in mod_of:
                held.add(mod_of[key])
                return
            name, result = None, None
            if held == want_mods and is_key(key, want_key):
                if state["undo_down"]:
                    return                       # keyboard auto-repeat while held: ignore
                state["undo_down"] = True
                name, result = "undo", self.unsay_press()
            elif held == {"ctrl", "shift"}:
                if is_key(key, "s"):
                    name, result = "safe", self.toggle_safe_mode()
                elif key == K.up:
                    name, result = "delay", self.set_delay(self.core.delay_setting() + 0.5)
                elif key == K.down:
                    name, result = "delay", self.set_delay(self.core.delay_setting() - 0.5)
            if name:
                emit(name, result)

        def on_release(key):
            released_mod = mod_of.get(key)
            if released_mod:
                held.discard(released_mod)
            if state["undo_down"] and (is_key(key, want_key) or released_mod in want_mods):
                state["undo_down"] = False
                self.unsay_release()
                emit("release", None)

        self._hotkeys = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._hotkeys.start()

    # controls (safe to call from any thread, including the Tkinter thread)
    def undo(self):
        """Tap: hide the delay window (plus the end of the current word)."""
        return self.core.undo()

    def unsay_press(self):
        """Key/button DOWN: unsay now, and stay hidden for as long as it is held."""
        r = self.core.undo()
        self.core.hold(True)
        return r

    def unsay_release(self):
        """Key/button UP: come back live (audio and video together)."""
        self.core.hold(False)

    def toggle_safe_mode(self):
        return self.core.toggle_safe_mode()

    def set_safe_mode(self, on):
        return self.core.set_safe_mode(on)

    def set_delay(self, seconds):
        return self.core.set_delay(seconds)

    def get_stats(self):
        s = self.core.stats()
        s["history"] = list(self.history)
        return s

    def current_delay(self):
        """Use this in the virtual camera loop so video stays in sync with audio."""
        return self.core.delay_seconds()


def main():
    p = argparse.ArgumentParser(description="Unsay microphone engine")
    p.add_argument("--audio-out", help="virtual cable output, e.g. 'BlackHole' or 'CABLE Input'")
    p.add_argument("--audio-in", default=None, help="real microphone (default: system mic)")
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--list-audio", action="store_true")
    a = p.parse_args()
    if a.list_audio:
        list_audio_devices()
        return
    if not a.audio_out:
        sys.exit("Give --audio-out (see --list-audio).")
    eng = UnsayEngine(out_name=a.audio_out, in_name=a.audio_in, rate=a.rate)
    names = eng.start()
    eng.start_hotkeys(on_event=lambda n, r: print(f"\n>> {n.upper()} {r}"))
    print(f"Unsay running: '{names[0]}' -> '{names[1]}'")
    print("Ctrl+Shift+Z = UNSAY   Ctrl+Shift+S = Safe Mode   Ctrl+C = quit")
    try:
        while True:
            s = eng.get_stats()
            mode = "SAFE" if s["safe_mode"] else "auto"
            talk = "talking" if s["speaking"] else "quiet  "
            print(f"\rdelay {s['delay']:.2f}s  {mode}  {talk}  undos {s['undo_count']}   ", end="")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        eng.stop()


if __name__ == "__main__":
    main()
