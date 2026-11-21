"""
video_engine.py - webcam -> virtual camera, kept in sync with the Unsay audio.

The video always shows the frame recorded at the same moment as the audio being
heard (shared timestamps), so lips stay in sync. When the user UNSAYs, the hidden
window is shown as a heavily blurred freeze.

Needs: opencv-python, pyvirtualcam, and the OBS Studio virtual camera driver.
"""

import bisect
import sys
import threading
import time
from collections import deque

import numpy as np


class VideoSync:
    """Webcam -> virtual camera, showing the frame that matches the audio being heard."""

    def __init__(self, core, camera=0, width=1280, height=720, fps=30,
                 device=None, jpeg=True, keep_seconds=4.0, badge=True):
        self.core = core
        self.camera = camera
        self.width, self.height, self.fps = width, height, fps
        self.device = device
        self.jpeg = jpeg
        self.keep = keep_seconds     # must be more than the largest delay (Safe Mode)
        self.badge = badge
        self.running = False
        self.error = None
        self.status = "stopped"
        self.virtual_name = None
        self.preview = None          # latest frame sent to the virtual camera
        self._thread = None
        self._cap = None

    def _pack(self, f):
        import cv2
        return cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])[1] if self.jpeg else f

    def _unpack(self, f):
        import cv2
        return cv2.imdecode(f, cv2.IMREAD_COLOR) if self.jpeg else f

    def _draw_badge(self, img):
        import cv2
        cv2.rectangle(img, (12, 12), (190, 46), (0, 0, 0), -1)
        cv2.putText(img, "UNSAY ON", (22, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def _open_camera(self):
        # Must run on the MAIN thread: on macOS the camera permission prompt
        # can only appear from the main thread.
        import cv2
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.camera, backend)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def _run(self):
        import pyvirtualcam
        from pyvirtualcam import PixelFormat

        cap = self._cap
        ok, frame = False, None
        for _ in range(60):          # camera warm-up: retry for ~3 s
            ok, frame = cap.read()
            if ok and frame is not None:
                break
            time.sleep(0.05)
        if not ok:
            self.error = "Camera opened but gave no frames"
            cap.release()
            self.running = False
            return
        h, w = frame.shape[:2]
        black = np.zeros((h, w, 3), dtype=np.uint8)

        times, frames = deque(), deque()
        freeze_key, freeze_img = None, None
        try:
            with pyvirtualcam.Camera(width=w, height=h, fps=self.fps,
                                     fmt=PixelFormat.BGR, device=self.device) as cam:
                self.virtual_name = cam.device
                self.status = "running"
                while self.running:
                    now = time.monotonic()
                    times.append(now)
                    frames.append(self._pack(frame))
                    keep = max(self.keep, self.core.max_frames * self.core.frame_s + 2.0,
                               self.core.safe_frames * self.core.frame_s + 2.0)
                    while times and times[0] < now - keep:
                        times.popleft()
                        frames.popleft()

                    target, frozen = self.core.video_target()
                    if target is None:
                        out = black                         # audio not playing yet
                    else:
                        if frozen and freeze_key == target and freeze_img is not None:
                            out = freeze_img
                        else:
                            i = bisect.bisect_right(times, target) - 1
                            out = self._unpack(frames[max(i, 0)])
                            if frozen:
                                import cv2
                                small = cv2.resize(out, (16, 9), interpolation=cv2.INTER_AREA)
                                out = cv2.resize(small, (out.shape[1], out.shape[0]),
                                                 interpolation=cv2.INTER_LINEAR)
                                freeze_key, freeze_img = target, out
                        if not frozen:
                            freeze_key, freeze_img = None, None
                    if self.badge:
                        out = self._draw_badge(out.copy())
                    cam.send(out)
                    self.preview = out           # what other people see right now (for the GUI)

                    ok, frame = cap.read()   # blocks until the next frame: paces the loop
                    if not ok:
                        # macOS sometimes drops frames: retry ~5 s, reopening the camera each second
                        fails = 0
                        while self.running and not ok and fails < 100:
                            time.sleep(0.05)
                            fails += 1
                            if fails % 20 == 0:
                                cap.release()
                                cap = self._open_camera() or cap
                            ok, frame = cap.read()
                        if not ok:
                            self.error = "Lost the camera feed (is another app using the camera?)"
                            break
        except Exception as e:  # usually: OBS virtual camera driver not installed
            self.error = f"Virtual camera error: {e}"
        finally:
            cap.release()
            self.running = False
            self.status = "stopped"

    def start(self):
        self.error = None
        self._cap = self._open_camera()
        if self._cap is None:
            self.error = (f"Could not open camera {self.camera}. On macOS allow your terminal in "
                          "System Settings > Privacy & Security > Camera, then run again.")
            self.status = "stopped"
            return
        self.running = True
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=2)


def list_cameras(max_index=6):
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    for i in range(max_index):
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            ok, f = cap.read()
            if ok:
                print(f"  camera {i}: {f.shape[1]}x{f.shape[0]}")
        cap.release()
