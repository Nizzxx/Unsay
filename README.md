# Unsay

**Say it. Regret it. Unsay it.**

Unsay gives every app (Zoom, Discord, Meet, Teams…) a new **virtual microphone** and a new
**virtual camera**. Everything you say and do reaches them a short delay later (you choose it).
If you slip — a password, a rude comment, a gesture — press **UNSAY** and the other people
never hear or see it.

- **Tap** UNSAY: the last *delay* seconds never happened (voice silenced, video blurred-frozen, in sync).
- **Hold** UNSAY: you stay hidden until you let go.
- Works from the big button in the app or a global key you choose (default `Ctrl+Shift+Z`),
  even while Zoom/Discord is the active window.

## How it works

```
real mic    -> safety buffer (delay + UNSAY) -> virtual mic    (BlackHole / VB-Cable)
real webcam -> same timeline as the audio    -> virtual camera (OBS Virtual Camera)
```

Every 20 ms audio frame carries the time it was recorded. The camera always shows the frame
recorded at the same moment as the audio being played, so lips stay in sync, and an UNSAY hides
exactly the same time window in both.

## Setup (once)

1. Install the drivers:
   - **macOS:** [BlackHole 2ch](https://existential.audio/blackhole/) (`brew install blackhole-2ch`) and
     OBS Studio (`brew install --cask obs`). Open OBS once, click **Start Virtual Camera**, then **Stop**.
   - **Windows:** [VB-Cable](https://vb-audio.com/Cable/) and OBS Studio (same OBS step).
2. Install the Python packages:
   ```
   pip install -r Requirements.txt
   ```
3. **macOS permissions** (System Settings → Privacy & Security): allow your terminal/IDE under
   **Microphone**, **Camera**, **Accessibility** and **Input Monitoring** (the last two are for the global key).

## Windows quick start

1. Install **Python 3.11 or 3.12** from python.org (tick *Add python.exe to PATH*). Newer Pythons may
   not have ready-made packages for pyvirtualcam yet.
2. Install **VB-Cable** (right-click the installer > *Run as administrator*), then restart the PC.
3. Install **OBS Studio**, open it once, click **Start Virtual Camera**, then **Stop**, close OBS.
4. In the project folder:
   ```
   py -3.12 -m pip install -r Requirements.txt
   py -3.12 app.py
   ```
5. In Discord/Zoom: **Input = CABLE Output (VB-Audio Virtual Cable)**, **Camera = OBS Virtual Camera**.
   Set the camera there BEFORE pressing Start: Windows lets only one app use the real webcam.

## Run

```
python app.py          # desktop app   (macOS: python3 app.py)
python unsay.py --audio-out "BlackHole" --camera 2 --delay 1.5 --undo-key f9   # command-line version
```

In the app: pick your real camera (use **Find**; don't pick OBS Virtual Camera itself) and an
Unsay window, then **Start**. Unsay detects your microphone and the virtual cable by itself.
Smart delay: on for conversations, off for interviews and meetings.

Then in Zoom/Discord choose:
- **Microphone:** `BlackHole 2ch` (Mac) / `CABLE Output` (Windows)
- **Camera:** `OBS Virtual Camera`
- Turn off noise suppression / echo cancellation in the call app for the cleanest cuts.

## Project structure

| File | What it does |
|---|---|
| `app.py` | Desktop app (CustomTkinter): settings, live preview, UNSAY button, Start/Stop |
| `unsay.py` | Command-line version |
| `modules/devices.py` | `VirtualMicrophone` and `VirtualCamera` classes (start / stop / unsay) + device lists |
| `modules/audio_engine.py` | Audio buffer, speech detection, delay, UNSAY, hold-to-hide, global hotkeys |
| `modules/video_engine.py` | Webcam → virtual camera, synced to the audio, blurred freeze on UNSAY |
| `modules/settings_manager.py` | Saves your settings to `settings.json` |
| `modules/hotkey_process.py` | Global UNSAY key in a helper process (pynput + Tkinter in one process crashes on macOS) |

## Tech stack

Python · sounddevice + NumPy (audio) · OpenCV + pyvirtualcam (video) · BlackHole / VB-Cable and the
OBS virtual camera (virtual devices) · pynput (global hotkeys) · CustomTkinter (interface).
Everything runs locally: no cloud, no tokens.
