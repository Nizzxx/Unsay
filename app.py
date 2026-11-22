"""
app.py - Unsay desktop app (CustomTkinter).

Run:  python app.py      (macOS: python3.10 app.py, or double-click Unsay.command)

What it does
  * Feeds a VIRTUAL MICROPHONE (BlackHole / VB-Cable) and a VIRTUAL CAMERA
    (OBS Virtual Camera) that any app can use: Zoom, Discord, Meet, Teams...
  * Everything you say/do reaches them after a short delay.
  * UNSAY: tap = the last moments never happened (audio + video, in sync).
           hold = you stay hidden until you let go.
  * Smart delay: full delay while you talk, only 0.5 s while you listen.

Threading rule: audio/video run on their own threads; this window only READS
their stats inside self.after(...), so Tkinter is never touched from another thread.
"""

import os
import sys
import threading
import time
from tkinter import messagebox

import customtkinter as ctk

from modules import settings_manager
from modules.audio_engine import parse_combo
from modules.devices import (VirtualCamera, VirtualMicrophone, auto_microphone,
                             default_virtual_output, list_cameras, microphone_label)

ctk.set_appearance_mode("dark")

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(HERE, "assets", "icon.png")
PROFILE = "cam0"
CAMERA_OFF = "Off (audio only)"
NO_CABLE = "no virtual cable found"

# ---- look -------------------------------------------------------------------
WIDTH, HEIGHT = 1000, 700
PREVIEW_W, PREVIEW_H = 464, 261

BG = "#0e1014"
CARD = "#161a21"
FIELD = "#1f242d"
FIELD_HOVER = "#272d38"
BORDER = "#262c36"
TEXT = "#e9ecf1"
MUTED = "#8a93a3"
FAINT = "#5c6472"
ACCENT = "#ff4757"
ACCENT_HOVER = "#e2394a"
GREEN = "#2ecc71"
GREEN_HOVER = "#25a85c"
ORANGE = "#ffa502"

PILLS = {   # state -> (text, text colour, background)
    "stopped": ("●  STOPPED", MUTED, "#222730"),
    "live": ("●  LIVE", GREEN, "#123322"),
    "unsaid": ("●  UNSAID", ORANGE, "#3a2c0e"),
    "hidden": ("●  HIDDEN", ACCENT, "#3b141a"),
}

if sys.platform == "darwin":
    FAMILY = "Helvetica Neue"
elif sys.platform == "win32":
    FAMILY = "Segoe UI"
else:
    FAMILY = "Helvetica"


def font(size, bold=False):
    return (FAMILY, size, "bold") if bold else (FAMILY, size)


def error_message(title, message):
    messagebox.showerror(title=title, message=message)


class UnsayApp(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        self.title("Unsay")
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        self._set_window_icon()

        self.settings = settings_manager.get_settings(PROFILE)
        self.mic = None
        self.cam = None
        self.preview_on = True
        self.audio_in, self.audio_out = None, None
        self._save_job = None

        self._build_ui()
        self._load_devices()
        self._apply_settings_to_widgets()
        self._set_pill("stopped")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.refresh)

    # ================================================================== UI ==
    def _set_window_icon(self):
        try:                       # Unsay icon instead of the Python rocket (window + Dock)
            import tkinter
            img = tkinter.PhotoImage(file=ICON_PATH)
            if img.width() > 256:
                img = img.subsample(max(1, img.width() // 256))
            self._icon = img
            self.iconphoto(True, img)
        except Exception:
            pass

    def _card(self, parent, width, height):
        f = ctk.CTkFrame(parent, width=width, height=height, fg_color=CARD, corner_radius=18,
                         border_width=1, border_color=BORDER)
        f.pack_propagate(False)
        return f

    def _section(self, parent, text):
        ctk.CTkLabel(parent, text=text, font=font(11, True), text_color=FAINT,
                     anchor="w").pack(fill="x", padx=22, pady=(14, 4))

    def _divider(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=BORDER).pack(fill="x", padx=22, pady=(10, 0))

    def _switch(self, parent, title, subtitle, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(6, 0))
        sw = ctk.CTkSwitch(row, text="", width=46, command=command, progress_color=ACCENT,
                           button_color=TEXT, button_hover_color="#ffffff", fg_color=FIELD_HOVER)
        sw.pack(side="right")
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text, text=title, font=font(14), text_color=TEXT, anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(text, text=subtitle, font=font(11), text_color=MUTED,
                         anchor="w").pack(fill="x")
        return sw

    def _slider(self, parent, title, lo, hi, steps, command):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(6, 0))
        ctk.CTkLabel(head, text=title, font=font(14), text_color=TEXT, anchor="w").pack(side="left")
        value = ctk.CTkLabel(head, text="", font=font(14, True), text_color=TEXT)
        value.pack(side="right")
        s = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, command=command, height=16,
                          progress_color=ACCENT, button_color=TEXT, button_hover_color="#ffffff",
                          fg_color=FIELD_HOVER)
        s.pack(fill="x", padx=16, pady=(2, 0))
        return s, value

    def _build_ui(self):
        # ---------------------------------------------------------- header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(20, 12))
        try:
            from PIL import Image
            logo = ctk.CTkImage(light_image=Image.open(ICON_PATH), dark_image=Image.open(ICON_PATH),
                                size=(40, 40))
            ctk.CTkLabel(header, text="", image=logo).pack(side="left")
            self._logo = logo
        except Exception:
            pass
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=12)
        ctk.CTkLabel(titles, text="Unsay", font=font(26, True), text_color=TEXT,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(titles, text="Say it. Regret it. Unsay it.", font=font(13), text_color=MUTED,
                     anchor="w").pack(fill="x")
        self.audio_label = ctk.CTkLabel(header, text="", font=font(12), text_color=MUTED,
                                        fg_color=CARD, corner_radius=10, height=30)
        self.audio_label.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28)

        # ------------------------------------------------------ left card
        left = self._card(body, 400, 570)
        left.pack(side="left", fill="y")

        self._section(left, "PROTECTION")
        self.delay_slider, self.delay_value = self._slider(left, "Unsay window", 0.5, 5.0, 9,
                                                           self.on_delay)
        self.smart_switch = self._switch(left, "Smart delay", "On for conversations, off for interviews & meetings",
                                         self.on_smart)
        self.safe_switch = self._switch(left, "Safe mode", "+1 s extra for sensitive moments",
                                        self.on_safe)
        self._divider(left)

        self._section(left, "CONTROLS")
        key_row = ctk.CTkFrame(left, fg_color="transparent")
        key_row.pack(fill="x", padx=22, pady=(6, 0))
        ctk.CTkLabel(key_row, text="Unsay key", font=font(14), text_color=TEXT).pack(side="left")
        ctk.CTkButton(key_row, text="Set", width=52, height=32, corner_radius=10, font=font(13, True),
                      fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT,
                      command=self.apply_key).pack(side="right")
        self.key_entry = ctk.CTkEntry(key_row, width=150, height=32, corner_radius=10, font=font(13),
                                      fg_color=FIELD, border_color=BORDER, text_color=TEXT)
        self.key_entry.pack(side="right", padx=8)
        self.key_entry.bind("<Return>", lambda e: self.apply_key())

        cam_row = ctk.CTkFrame(left, fg_color="transparent")
        cam_row.pack(fill="x", padx=22, pady=(10, 0))
        ctk.CTkLabel(cam_row, text="Camera", font=font(14), text_color=TEXT).pack(side="left")
        ctk.CTkButton(cam_row, text="Find", width=52, height=32, corner_radius=10, font=font(13, True),
                      fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT,
                      command=self.find_cameras).pack(side="right")
        self.cam_menu = ctk.CTkOptionMenu(cam_row, values=[CAMERA_OFF, "0", "1", "2", "3"], width=150,
                                          height=32, corner_radius=10, font=font(13),
                                          fg_color=FIELD, button_color=FIELD_HOVER,
                                          button_hover_color=BORDER, text_color=TEXT,
                                          dropdown_fg_color=FIELD, dropdown_hover_color=FIELD_HOVER,
                                          dropdown_text_color=TEXT, command=lambda v: self._save_soon())
        self.cam_menu.pack(side="right", padx=8)

        self.sync_slider, self.sync_value = self._slider(left, "Lip sync", 0.0, 0.5, 10, self.on_lip_sync)
        self.badge_switch = self._switch(left, 'Show "UNSAY ON" badge', "Tells others you use a delay",
                                         self._save_soon)

        # ----------------------------------------------------- right card
        right = self._card(body, 516, 570)
        right.pack(side="right", fill="y")

        top = ctk.CTkFrame(right, fg_color="transparent")
        top.pack(fill="x", padx=22, pady=(16, 10))
        ctk.CTkLabel(top, text="WHAT OTHERS SEE", font=font(11, True), text_color=FAINT).pack(side="left")
        self.pill = ctk.CTkLabel(top, text="", font=font(12, True), corner_radius=12, height=26, width=110)
        self.pill.pack(side="right")

        from PIL import Image, ImageDraw
        self._mask = Image.new("L", (PREVIEW_W, PREVIEW_H), 0)
        ImageDraw.Draw(self._mask).rounded_rectangle((0, 0, PREVIEW_W - 1, PREVIEW_H - 1), radius=14,
                                                     fill=255)
        self._blank = self._rounded(Image.new("RGB", (PREVIEW_W, PREVIEW_H), FIELD))
        # ONE image for the whole app life; only its picture is swapped (never image=None:
        # CustomTkinter then points at a deleted Tk image -> "pyimageN doesn't exist")
        self._preview_img = ctk.CTkImage(light_image=self._blank, dark_image=self._blank,
                                         size=(PREVIEW_W, PREVIEW_H))
        self.preview_label = ctk.CTkLabel(right, text="Press Start", image=self._preview_img,
                                          compound="center", font=font(15), text_color=MUTED,
                                          width=PREVIEW_W, height=PREVIEW_H)
        self.preview_label.pack()

        meter = ctk.CTkFrame(right, fg_color="transparent")
        meter.pack(fill="x", padx=26, pady=(14, 0))
        ctk.CTkLabel(meter, text="Delay now", font=font(12), text_color=MUTED).pack(side="left")
        self.delay_now = ctk.CTkLabel(meter, text="-", font=font(12, True), text_color=TEXT, width=48)
        self.delay_now.pack(side="right")
        self.delay_bar = ctk.CTkProgressBar(meter, height=8, corner_radius=4, progress_color=ACCENT,
                                            fg_color=FIELD_HOVER)
        self.delay_bar.pack(side="left", fill="x", expand=True, padx=12)
        self.delay_bar.set(0)

        self.stats_label = ctk.CTkLabel(right, text="", font=font(12), text_color=MUTED)
        self.stats_label.pack(pady=(6, 0))

        self.unsay_button = ctk.CTkButton(right, text="UNSAY", font=font(22, True), height=58,
                                          width=PREVIEW_W, corner_radius=16, fg_color=ACCENT,
                                          hover_color=ACCENT_HOVER, text_color="#ffffff")
        self.unsay_button.pack(pady=(10, 0))
        self.unsay_button.bind("<ButtonPress-1>", lambda e: self.mic and self.mic.unsay_press())
        self.unsay_button.bind("<ButtonRelease-1>", lambda e: self.mic and self.mic.unsay_release())
        ctk.CTkLabel(right, text="tap to erase the last moments  ·  hold to stay hidden",
                     font=font(11), text_color=FAINT).pack(pady=(4, 0))

        buttons = ctk.CTkFrame(right, fg_color="transparent")
        buttons.pack(pady=(10, 0))
        self.start_button = ctk.CTkButton(buttons, text="Start", width=300, height=44, corner_radius=14,
                                          font=font(16, True), fg_color=GREEN, hover_color=GREEN_HOVER,
                                          text_color="#08130c", command=self.toggle_start)
        self.start_button.pack(side="left")
        self.preview_button = ctk.CTkButton(buttons, text="Hide preview", width=152, height=44,
                                            corner_radius=14, font=font(13), fg_color=FIELD,
                                            hover_color=FIELD_HOVER, text_color=TEXT,
                                            command=self.toggle_preview)
        self.preview_button.pack(side="left", padx=(12, 0))

        # ---------------------------------------------------------- footer
        ctk.CTkLabel(self, text="In Discord / Zoom (once):  Microphone = BlackHole 2ch (Mac) or CABLE Output"
                                " (Windows)   ·   Camera = OBS Virtual Camera",
                     font=font(11), text_color=FAINT).pack(pady=(10, 12))

    def _rounded(self, img):
        from PIL import Image
        out = Image.new("RGB", (PREVIEW_W, PREVIEW_H), CARD)
        out.paste(img, (0, 0), self._mask)
        return out

    def _set_pill(self, state):
        text, color, bg = PILLS[state]
        if self.pill.cget("text") != text:
            self.pill.configure(text=text, text_color=color, fg_color=bg)

    # ============================================================ devices ==
    def _load_devices(self):
        try:
            self.audio_out = default_virtual_output()
            self.audio_in = auto_microphone()
            mic_text = microphone_label(self.audio_in)
        except Exception as e:  # PortAudio missing etc.
            self.audio_in, self.audio_out, mic_text = None, None, "?"
            error_message("Audio", f"Could not list audio devices:\n{e}")
        out_text = self.audio_out or NO_CABLE
        self.audio_label.configure(text=f"   {mic_text}   →   {out_text}   ",
                                   text_color=MUTED if self.audio_out else ACCENT)

    def find_cameras(self):
        if self.mic and self.mic.running:
            error_message("Camera", "Stop Unsay first, then search for cameras.")
            return
        self.cam_menu.configure(values=[CAMERA_OFF, "searching..."])
        self.update_idletasks()
        try:
            found = [str(i) for i in list_cameras()]
        except Exception as e:
            found = []
            error_message("Camera", f"Could not list cameras:\n{e}")
        self.cam_menu.configure(values=[CAMERA_OFF] + found)
        if found and self.cam_menu.get() not in found:
            self.cam_menu.set(found[0])
        self._save_soon()
        messagebox.showinfo("Cameras", f"Working cameras: {', '.join(found) or 'none'}\n\n"
                                       "One of them is OBS Virtual Camera itself: don't pick that one "
                                       "(your video would loop). Try each and look at the preview.")

    # =========================================================== settings ==
    def _apply_settings_to_widgets(self):
        s = self.settings
        cam = str(s["video"]["camera"]) if s["video"]["enabled"] else CAMERA_OFF
        values = list(self.cam_menu.cget("values"))
        if cam not in values:
            self.cam_menu.configure(values=values + [cam])
        self.cam_menu.set(cam)
        delay = float(s["controls"]["delay"])
        self.delay_slider.set(delay)
        self.delay_value.configure(text=f"{delay:.1f} s")
        sync = float(s["video"].get("lip_sync", 0.0))
        self.sync_slider.set(sync)
        self.sync_value.configure(text=f"{sync:.2f} s")
        self.key_entry.insert(0, s["controls"]["undo_key"])
        for switch, on in ((self.badge_switch, s["video"]["badge"]),
                           (self.smart_switch, s["controls"].get("smart_delay", True))):
            switch.select() if on else switch.deselect()

    def _read_widgets(self):
        s = self.settings
        s["audio"]["audio_in"] = self.audio_in
        s["audio"]["audio_out"] = self.audio_out
        cam = self.cam_menu.get()
        s["video"]["enabled"] = cam.isdigit()
        if cam.isdigit():
            s["video"]["camera"] = int(cam)
        s["video"]["badge"] = bool(self.badge_switch.get())
        s["video"]["lip_sync"] = round(float(self.sync_slider.get()), 2)
        s["controls"]["delay"] = round(float(self.delay_slider.get()), 1)
        s["controls"]["smart_delay"] = bool(self.smart_switch.get())
        key = self.key_entry.get().strip()
        try:
            parse_combo(key)
        except ValueError:                 # half-typed / invalid: keep the last working key
            key = s["controls"]["undo_key"]
        s["controls"]["undo_key"] = key
        return s

    def _save_now(self):
        self._save_job = None
        try:
            settings_manager.save_settings(self._read_widgets(), PROFILE)
        except OSError:
            pass

    def _save_soon(self, *_):
        """Every change is saved automatically (half a second after you stop moving things)."""
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._save_now)

    # =========================================================== controls ==
    def toggle_start(self):
        # ignore clicks while starting/stopping, and double-clicks right after one
        if getattr(self, "_busy", False) or time.monotonic() - getattr(self, "_last_toggle", 0) < 0.6:
            return
        self._last_toggle = time.monotonic()
        if self.mic and self.mic.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self._load_devices()            # re-detect (headset plugged in since launch?)
        s = self._read_widgets()
        self._save_now()
        if not s["audio"]["audio_out"]:
            error_message("Virtual microphone missing",
                          "No virtual audio cable found.\n\nMac: brew install blackhole-2ch\n"
                          "Windows: install VB-Cable (run as administrator)\n\nThen restart the computer.")
            return
        try:
            parse_combo(s["controls"]["undo_key"])
        except ValueError as e:
            error_message("Unsay key", str(e))
            return

        self._busy = True
        self.start_button.configure(text="Starting...", state="disabled")
        self.update_idletasks()
        self.mic = VirtualMicrophone(out_name=s["audio"]["audio_out"], in_name=s["audio"]["audio_in"],
                                     delay=s["controls"]["delay"])
        try:
            self.mic.start()
        except Exception as e:
            self.mic = None
            self._busy = False
            self.start_button.configure(text="Start", state="normal")
            error_message("Microphone", f"Could not start the microphone:\n{e}")
            return
        self.mic.core.set_smart(s["controls"]["smart_delay"])
        self.mic.core.set_av_offset(s["video"]["lip_sync"])
        if self.safe_switch.get():
            self.mic.set_safe_mode(True)

        self.cam = None
        if s["video"]["enabled"]:
            v = s["video"]
            self.cam = VirtualCamera(self.mic, camera=v["camera"], width=v["width"],
                                     height=v["height"], fps=v["fps"], badge=v["badge"])
            err = self.cam.start()     # main thread (macOS camera permission)
            if err:
                error_message("Camera", f"{err}\n\nAudio keeps working. Try another camera number.")

        try:
            self.mic.start_hotkeys(s["controls"]["undo_key"])
        except Exception as e:
            error_message("Unsay key",
                          f"The keyboard shortcut is not available:\n{e}\n\n"
                          "Use the UNSAY button instead. On macOS, allow your terminal in "
                          "Privacy & Security > Accessibility and Input Monitoring.")

        self._busy = False
        self._last_toggle = time.monotonic()
        self.start_button.configure(text="Stop", state="normal", fg_color=FIELD,
                                    hover_color=FIELD_HOVER, text_color=TEXT)
        self.cam_menu.configure(state="disabled")

    def stop(self, wait=False):
        cam, mic = self.cam, self.mic
        self.cam = None
        self.mic = None

        def shut_down():
            if cam:
                cam.stop()
            if mic:
                mic.stop()

        if wait:                                   # closing the app: finish here
            shut_down()
        else:                                      # normal Stop: never freeze the window
            self._busy = True
            self.start_button.configure(text="Stopping...", state="disabled")
            worker = threading.Thread(target=shut_down, daemon=True)
            worker.start()
            self._wait_stopped(worker)
        self.start_button.configure(fg_color=GREEN, hover_color=GREEN_HOVER, text_color="#08130c")
        self.cam_menu.configure(state="normal")
        self._show_placeholder("Press Start")
        self._set_pill("stopped")
        self.stats_label.configure(text="")
        self.delay_now.configure(text="-")
        self.delay_bar.set(0)
        self._bar = 0

    def _wait_stopped(self, worker):
        if worker.is_alive():
            self.after(50, lambda: self._wait_stopped(worker))
            return
        self._busy = False
        self._last_toggle = time.monotonic()
        self.start_button.configure(text="Start", state="normal")

    def on_delay(self, value):
        value = round(float(value), 1)
        self.delay_value.configure(text=f"{value:.1f} s")
        if self.mic and self.mic.running:
            self.mic.set_delay(value)
        self._save_soon()

    def on_smart(self):
        if self.mic and self.mic.running:
            self.mic.core.set_smart(bool(self.smart_switch.get()))
        self._save_soon()

    def on_safe(self):
        if self.mic and self.mic.running:
            self.mic.set_safe_mode(bool(self.safe_switch.get()))

    def on_lip_sync(self, value):
        value = round(float(value), 2)
        self.sync_value.configure(text=f"{value:.2f} s")
        if self.mic and self.mic.running:
            self.mic.core.set_av_offset(value)
        self._save_soon()

    def apply_key(self):
        key = self.key_entry.get().strip()
        try:
            parse_combo(key)
        except ValueError as e:
            error_message("Unsay key", str(e))
            self.key_entry.delete(0, "end")
            self.key_entry.insert(0, self.settings["controls"]["undo_key"])
            return
        if self.mic and self.mic.running:
            try:
                self.mic.start_hotkeys(key)
            except Exception as e:
                error_message("Unsay key", str(e))
                return
        self._save_now()
        self.focus_set()
        self.stats_label.configure(text=f"Unsay key set to {key.upper()}")

    def toggle_preview(self):
        self.preview_on = not self.preview_on
        self.preview_button.configure(text="Hide preview" if self.preview_on else "Show preview")
        if not self.preview_on:
            self._show_placeholder("Preview hidden")

    # ============================================================ refresh ==
    def refresh(self):
        try:
            self._refresh_status()
            self._tick = getattr(self, "_tick", 0) + 1
            if self._tick % 2 == 0:            # preview 5x a second is plenty
                self._refresh_preview()
        finally:
            self.after(100, self.refresh)

    @staticmethod
    def _set_text(widget, text):
        """Redraw a label only when its words change (redraws are what make clicks lag)."""
        if widget.cget("text") != text:
            widget.configure(text=text)

    def _refresh_status(self):
        stats = self.mic.stats() if self.mic else None
        if not stats:
            return
        if stats["holding"]:
            self._set_pill("hidden")
        elif stats["muting"] or (self.cam and self.cam.frozen):
            self._set_pill("unsaid")
        else:
            self._set_pill("live")

        # keep switches/slider in step with the global keys (Ctrl+Shift+S, Ctrl+Shift+Up/Down)
        if bool(self.safe_switch.get()) != stats["safe_mode"]:
            self.safe_switch.select() if stats["safe_mode"] else self.safe_switch.deselect()
        if not stats["safe_mode"] and abs(self.delay_slider.get() - stats["delay_setting"]) > 0.05:
            self.delay_slider.set(stats["delay_setting"])
            self.delay_value.configure(text=f"{stats['delay_setting']:.1f} s")

        self._set_text(self.delay_now, f"{stats['delay']:.1f} s")
        bar = round(min(1.0, stats["delay"] / 5.0), 2)
        if bar != getattr(self, "_bar", None):
            self._bar = bar
            self.delay_bar.set(bar)

        parts = ["talking" if stats["speaking"] else "listening",
                 f"{stats['undo_count']} unsay{'' if stats['undo_count'] == 1 else 's'}"]
        if stats["safe_mode"]:
            parts.append("SAFE MODE")
        text = "   ·   ".join(parts)
        if self.cam and self.cam.error:
            text += f"\nVideo: {self.cam.error}"
        if self.mic.hotkey_error:
            text += f"\nKey: {self.mic.hotkey_error} (use the button)"
        self._set_text(self.stats_label, text)

    def _refresh_preview(self):
        if not (self.preview_on and self.cam and self.cam.preview is not None):
            if self.mic and self.mic.running and self.preview_on and not self.cam:
                if self.preview_label.cget("text") != "Audio only":
                    self._show_placeholder("Audio only")
            return
        import cv2
        from PIL import Image
        frame = cv2.resize(self.cam.preview, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA)
        img = self._rounded(Image.fromarray(frame[:, :, ::-1].copy()))      # BGR -> RGB
        self._preview_img.configure(light_image=img, dark_image=img)
        if self.preview_label.cget("text"):
            self.preview_label.configure(text="")

    def _show_placeholder(self, text):
        self._preview_img.configure(light_image=self._blank, dark_image=self._blank)
        self.preview_label.configure(text=text)

    def on_close(self):
        try:
            self._save_now()
            self.stop(wait=True)
        finally:
            self.destroy()


if __name__ == "__main__":
    import signal
    if hasattr(signal, "SIGTSTP"):          # Ctrl+Z in the terminal must not freeze the app
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    app = UnsayApp()
    app.mainloop()
