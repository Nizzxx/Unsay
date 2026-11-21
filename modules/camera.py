"""
delayed_cam.py - expose your webcam (and optionally your mic) as a virtual
camera / virtual microphone, both delayed by N seconds.

Video setup:
    pip install opencv-python pyvirtualcam numpy

  Windows / macOS : install OBS Studio (pyvirtualcam uses its virtual camera
                    driver). Don't run OBS's own "Start Virtual Camera" at the
                    same time as this script.
  Linux           : sudo apt install v4l2loopback-dkms
                    sudo modprobe v4l2loopback devices=1 video_nr=10 card_label="Delayed Cam" exclusive_caps=1
                    then run with --device /dev/video10

Audio setup (optional, adds the delayed sound):
    pip install sounddevice

  A virtual camera cannot carry sound, so the delayed audio is played into a
  virtual audio cable, which apps then see as a microphone:
  Windows : install VB-Cable (vb-audio.com/Cable), use --audio-out "CABLE Input",
            and pick "CABLE Output" as the microphone in your apps.
  macOS   : install BlackHole 2ch, use --audio-out "BlackHole",
            and pick "BlackHole 2ch" as the microphone in your apps.
  Linux   : pactl load-module module-null-sink sink_name=delayed
            use --audio-out "delayed", and pick "Monitor of Null Output" as the
            microphone in your apps.

Usage:
    python delayed_cam.py                                   # video only
    python delayed_cam.py --list                            # list cameras
    python delayed_cam.py --list-audio                      # list audio devices
    python delayed_cam.py --audio-out "CABLE Input"         # video + audio
    python delayed_cam.py --camera 1 --delay 5 --jpeg --audio-out "BlackHole"

Press Ctrl+C here to stop.
"""

import argparse
import sys
import time
from collections import deque

import cv2
import numpy as np
import pyvirtualcam
from pyvirtualcam import PixelFormat


def parse_args():
    p = argparse.ArgumentParser(description="Virtual camera (and mic) with a delay.")
    p.add_argument("--camera", type=int, default=0, help="physical camera index (default 0)")
    p.add_argument("--delay", type=float, default=3.0, help="delay in seconds (default 3)")
    p.add_argument("--width", type=int, default=1280, help="requested capture width")
    p.add_argument("--height", type=int, default=720, help="requested capture height")
    p.add_argument("--fps", type=int, default=30, help="frames per second (default 30)")
    p.add_argument("--device", default=None, help="virtual camera device (Linux: /dev/videoN)")
    p.add_argument("--jpeg", action="store_true",
                   help="store buffered frames as JPEG to cut RAM use (costs some CPU)")
    p.add_argument("--list", action="store_true", help="list available camera indices and exit")
    p.add_argument("--audio-out", default=None,
                   help="enable audio delay; output device name (substring) or index, "
                        "normally a virtual audio cable")
    p.add_argument("--audio-in", default=None,
                   help="microphone name (substring) or index (default: system default mic)")
    p.add_argument("--audio-rate", type=int, default=48000,
                   help="audio sample rate (default 48000; try 44100 if the device refuses it)")
    p.add_argument("--list-audio", action="store_true", help="list audio devices and exit")
    return p.parse_args()


def open_camera(index):
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    return cv2.VideoCapture(index, backend)


def list_cameras(max_index=10):
    print("Probing cameras...")
    found = False
    for i in range(max_index):
        cap = open_camera(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                print(f"  index {i}: {w}x{h}")
                found = True
        cap.release()
    if not found:
        print("  no cameras found")


def list_audio_devices():
    import sounddevice as sd
    print("Audio devices (index: name  [inputs/outputs]):")
    for i, d in enumerate(sd.query_devices()):
        print(f"  {i}: {d['name']}  [{d['max_input_channels']} in / {d['max_output_channels']} out]")


def find_device(sd, name, kind):
    """Return a device index from an index, a name substring, or the default."""
    if name is None:
        idx = sd.default.device[0 if kind == "input" else 1]
        if idx is None or idx < 0:
            sys.exit(f"No default audio {kind} device. Use --list-audio.")
        return idx
    if str(name).isdigit():
        return int(name)
    key = f"max_{kind}_channels"
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d[key] > 0:
            return i
    sys.exit(f'No audio {kind} device matching "{name}". Use --list-audio.')


class AudioDelay:
    """Records the mic and plays it to an output device N seconds later."""

    BLOCK = 1024

    def __init__(self, delay, in_name, out_name, rate):
        import sounddevice as sd
        self.sd = sd
        self.rate = rate
        self.target = max(1, round(delay * rate / self.BLOCK))  # delay in blocks
        self.in_idx = find_device(sd, in_name, "input")
        self.out_idx = find_device(sd, out_name, "output")
        out_info = sd.query_devices(self.out_idx)
        self.out_ch = 2 if out_info["max_output_channels"] >= 2 else 1
        self.silence = np.zeros((self.BLOCK, 1), dtype=np.float32)
        # Pre-filled with silence, so the first audible block comes out after `delay`.
        self.queue = deque(self.silence for _ in range(self.target))
        self.in_stream = None
        self.out_stream = None

    def _on_input(self, indata, frames, time_info, status):
        self.queue.append(indata.copy())

    def _on_output(self, outdata, frames, time_info, status):
        # Output clock slower than the input clock: skip ahead to stay on target.
        while len(self.queue) > self.target + 4:
            self.queue.popleft()
        if not self.queue:
            # Output clock faster than the input clock: restore the full delay.
            outdata.fill(0)
            self.queue.extend(self.silence for _ in range(self.target))
            return
        block = self.queue.popleft()
        if len(block) != frames:
            fixed = np.zeros((frames, 1), dtype=np.float32)
            n = min(frames, len(block))
            fixed[:n] = block[:n]
            block = fixed
        outdata[:] = np.repeat(block, self.out_ch, axis=1)

    def start(self):
        sd = self.sd
        self.out_stream = sd.OutputStream(
            device=self.out_idx, samplerate=self.rate, channels=self.out_ch,
            dtype="float32", blocksize=self.BLOCK, callback=self._on_output)
        self.in_stream = sd.InputStream(
            device=self.in_idx, samplerate=self.rate, channels=1,
            dtype="float32", blocksize=self.BLOCK, callback=self._on_input)
        self.out_stream.start()
        self.in_stream.start()
        print(f"Audio: '{sd.query_devices(self.in_idx)['name']}' -> "
              f"'{sd.query_devices(self.out_idx)['name']}' @ {self.rate} Hz")

    def stop(self):
        for s in (self.in_stream, self.out_stream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass


def launch_virt_camera(width, height, fps, delay, key, sign):
    args = parse_args()

    if args.list:
        list_cameras()
        return
    if args.list_audio:
        list_audio_devices()
        return

    audio = None
    if args.audio_out:
        audio = AudioDelay(delay, args.audio_in, args.audio_out, args.audio_rate)

    cap = open_camera(args.camera)
    if not cap.isOpened():
        sys.exit(f"Could not open camera {args.camera}. Try --list.")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)

    ok, frame = cap.read()
    if not ok:
        cap.release()
        sys.exit("Camera opened but returned no frames.")

    # The camera may not honour the requested size, so use what it really gives.
    height, width = frame.shape[:2]
    black = np.zeros((height, width, 3), dtype=np.uint8)

    def pack(f):
        if args.jpeg:
            return cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 90])[1]
        return f

    def unpack(f):
        return cv2.imdecode(f, cv2.IMREAD_COLOR) if args.jpeg else f

    # Each entry is (capture_time, frame). Only frames newer than
    # (now - delay) plus one older "current" frame are kept, so memory is bounded.
    buffer = deque()

    try:
        with pyvirtualcam.Camera(width=width, height=height, fps=fps,
                                 fmt=PixelFormat.BGR, device=args.device) as cam:
            print(f"Virtual camera: {cam.device}  ({width}x{height} @ {fps} fps)")
            if audio:
                audio.start()
            print(f"Delay: {delay}s - black/silent until the buffer fills. Ctrl+C to quit.")

            while True:
                now = time.monotonic()
                buffer.append((now, pack(frame)))

                # Drop frames that are superseded by a newer frame that is
                # still old enough to be shown.
                target = now - delay
                while len(buffer) > 1 and buffer[1][0] <= target:
                    buffer.popleft()

                out = unpack(buffer[0][1]) if buffer[0][0] <= target else black
                cam.send(out)

                # read() blocks until the next camera frame, which paces the loop.
                ok, frame = cap.read()
                if not ok:
                    print("Lost the camera feed, stopping.")
                    break
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:  # e.g. no virtual camera driver, or audio device refused
        sys.exit(f"Error: {e}")
    finally:
        if audio:
            audio.stop()
        cap.release()
