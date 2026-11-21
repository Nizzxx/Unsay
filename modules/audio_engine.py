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
                if not speech and self.quiet_run >= self.pause_frames:
                    self.mute_until_pause = False
                    if self.freezes and self.freezes[-1][1] is None:
                        self.freezes[-1][1] = t      # video unfreezes from here
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
        """Delete everything still in the buffer back to the last pause."""
        with self.lock:
            items = list(self.queue)
            n = len(items)
            # Walk backwards to find a run of >= pause_frames silent frames.
            cut = 0                      # default: delete the whole buffer
            run = 0
            for i in range(n - 1, -1, -1):
                if not items[i][1]:
                    run += 1
                    if run >= self.pause_frames:
                        cut = i + run    # keep the pause, delete after it
                        break
                else:
                    run = 0
            removed = n - cut
            speech_removed = sum(1 for s in items[cut:] if s[1])
            if speech_removed == 0:
                # Nothing spoken is waiting (e.g. a visual mistake while silent):
                # hide EVERYTHING still in the buffer on video, up to this moment.
                first_ts = next((it[2] for it in items if it[2] is not None), None)
                if first_ts is not None:
                    self.freezes.append([first_ts, time.monotonic()])
                    self.freezes = self.freezes[-20:]
                self.mute_until_pause = True
                self.undo_count += 1
                self.last_undo_seconds = n * self.frame_s
                return self.last_undo_seconds
            kept = items[:cut]
            if kept:  # short fade on the last kept frame to avoid a click
                last, sp, lt = kept[-1]
                fade = np.linspace(1.0, 0.0, len(last), dtype=np.float32)
                kept[-1] = (last * fade, sp, lt)
            # replace deleted audio with silence but keep the timestamps,
            # so the video keeps its timing and knows which part to hide
            self.queue = deque(kept + [(self.silence, False, it[2]) for it in items[cut:]])
            first_ts = next((it[2] for it in items[cut:] if it[2] is not None), None)
            if first_ts is not None:
                self.freezes.append([first_ts, None])   # end set when mute stops
                self.freezes = self.freezes[-20:]
            self.mute_until_pause = True
            self.undo_count += 1
            self.last_undo_seconds = removed * self.frame_s
            return self.last_undo_seconds

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

    def start_hotkeys(self):
        """Global hotkeys that work while Zoom/Discord/Safari is the active window.
        Ctrl+Shift+Z (or Cmd+Shift+Z) = UNSAY, Ctrl+Shift+S = Safe Mode,
        Ctrl+Shift+Up/Down = delay +/- 0.5 s.
        macOS: allow Terminal in Privacy & Security > Accessibility AND Input Monitoring."""
        from pynput import keyboard
        K = keyboard.Key
        held = set()
        vks = {"darwin": {"z": 6, "s": 1}, "win32": {"z": 0x5A, "s": 0x53}}.get(sys.platform, {})

        def letter(key, c):
            ch = getattr(key, "char", None)
            if ch:
                if ch.lower() == c:
                    return True
                if len(ch) == 1 and ord(ch) == ord(c) - 96:   # Ctrl+letter control char
                    return True
            vk = getattr(key, "vk", None)
            return vk is not None and vk == vks.get(c)

        def on_press(key):
            if key in (K.ctrl, K.ctrl_l, K.ctrl_r):
                held.add("ctrl")
            elif key in (K.cmd, K.cmd_l, K.cmd_r):
                held.add("cmd")
            elif key in (K.shift, K.shift_l, K.shift_r):
                held.add("shift")
            elif "shift" in held and ("ctrl" in held or "cmd" in held):
                if letter(key, "z"):
                    self.undo()
                elif letter(key, "s") and "ctrl" in held:
                    self.toggle_safe_mode()
                elif key == K.up and "ctrl" in held:
                    self.set_delay(self.core.delay_setting() + 0.5)
                elif key == K.down and "ctrl" in held:
                    self.set_delay(self.core.delay_setting() - 0.5)

        def on_release(key):
            if key in (K.ctrl, K.ctrl_l, K.ctrl_r):
                held.discard("ctrl")
            elif key in (K.cmd, K.cmd_l, K.cmd_r):
                held.discard("cmd")
            elif key in (K.shift, K.shift_l, K.shift_r):
                held.discard("shift")

        self._hotkeys = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._hotkeys.start()

    # controls (safe to call from any thread, including the Tkinter thread)
    def undo(self):
        return self.core.undo()

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
