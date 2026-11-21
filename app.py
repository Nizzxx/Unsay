"""
app.py - Unsay desktop app (CustomTkinter).

Run:  python app.py      (macOS: python3.10 app.py)

What it does
  * Creates a VIRTUAL MICROPHONE (BlackHole / VB-Cable) and a VIRTUAL CAMERA
    (OBS Virtual Camera) that any app can use: Zoom, Discord, Meet, Teams...
  * Everything you say/do reaches them after a short delay you choose.
  * UNSAY: tap = the last <delay> seconds never happened (audio + video, in sync).
           hold = you stay hidden until you let go.
  * Works from the big button here or a global key (default Ctrl+Shift+Z).

Threading rule: audio/video run on their own threads; this window only READS
their stats inside self.after(...), so Tkinter is never touched from another thread.
"""

import customtkinter as ctk
from tkinter import messagebox

from modules import settings_manager
from modules.audio_engine import parse_combo
from modules.devices import (VirtualCamera, VirtualMicrophone, default_virtual_output,
                             list_cameras, list_microphones, list_virtual_outputs)

ctk.set_appearance_mode("light")

# Constants
WIDTH = 920
HEIGHT = 620
FONT1 = ("Verdana", 24, "bold")
FONT2 = ("Verdana", 16, "bold")
FONT3 = ("Verdana", 13)
PREVIEW_W, PREVIEW_H = 384, 216
PROFILE = "cam0"
SYSTEM_DEFAULT = "System default"
CAMERA_OFF = "Off (audio only)"
NO_CABLE = "(install BlackHole / VB-Cable)"

GREEN, ORANGE, RED, GRAY = "#2e7d32", "#ef6c00", "#c62828", "#757575"


def error_message(title: str, message: str):
    messagebox.showerror(title=title, message=message)


class UnsayApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Unsay")
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)

        self.settings = settings_manager.get_settings(PROFILE)
        self.mic = None
        self.cam = None
        self.preview_on = True

        self._build_ui()
        self._load_devices()
        self._apply_settings_to_widgets()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.refresh)

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        ctk.CTkLabel(self, text="Unsay", font=FONT1).pack(pady=(10, 0))
        ctk.CTkLabel(self, text="Say it. Regret it. Unsay it.", font=FONT3, text_color=GRAY).pack()

        main = ctk.CTkFrame(self, width=WIDTH - 40, height=HEIGHT - 150)
        main.pack_propagate(False)
        main.pack(pady=8)

        # ---- left: settings
        left = ctk.CTkFrame(main, width=(WIDTH - 40) / 2 - 15, height=HEIGHT - 170)
        left.grid(row=0, column=0, padx=(10, 5), pady=10)
        left.grid_propagate(False)
        ctk.CTkLabel(left, text="Settings", font=FONT2).grid(row=0, column=0, columnspan=3, pady=(10, 8))

        def label(row, text):
            ctk.CTkLabel(left, text=text, font=FONT3).grid(row=row, column=0, sticky="e", padx=(12, 6), pady=6)

        label(1, "Name :")
        self.name_entry = ctk.CTkEntry(left, placeholder_text="...", width=230)
        self.name_entry.grid(row=1, column=1, columnspan=2, sticky="w")

        label(2, "Microphone :")
        self.mic_menu = ctk.CTkOptionMenu(left, values=[SYSTEM_DEFAULT], width=230)
        self.mic_menu.grid(row=2, column=1, columnspan=2, sticky="w")

        label(3, "Send audio to :")
        self.out_menu = ctk.CTkOptionMenu(left, values=[NO_CABLE], width=230)
        self.out_menu.grid(row=3, column=1, columnspan=2, sticky="w")

        label(4, "Camera :")
        self.cam_menu = ctk.CTkOptionMenu(left, values=[CAMERA_OFF, "0", "1", "2", "3"], width=180)
        self.cam_menu.grid(row=4, column=1, sticky="w")
        ctk.CTkButton(left, text="Find", width=44, command=self.find_cameras).grid(row=4, column=2, sticky="w")

        label(5, "Delay :")
        delay_row = ctk.CTkFrame(left, fg_color="transparent")
        delay_row.grid(row=5, column=1, columnspan=2, sticky="w")
        self.delay_slider = ctk.CTkSlider(delay_row, from_=0.5, to=5.0, number_of_steps=9,
                                          width=170, command=self.on_delay)
        self.delay_slider.grid(row=0, column=0)
        self.delay_label = ctk.CTkLabel(delay_row, text="2.0s", font=FONT3, width=45)
        self.delay_label.grid(row=0, column=1, padx=6)

        label(6, "Unsay key :")
        self.key_entry = ctk.CTkEntry(left, width=180)
        self.key_entry.grid(row=6, column=1, sticky="w")
        ctk.CTkButton(left, text="Set", width=44, command=self.apply_key).grid(row=6, column=2, sticky="w")

        self.badge_switch = ctk.CTkSwitch(left, text='Show "UNSAY ON" badge', font=FONT3)
        self.badge_switch.grid(row=7, column=0, columnspan=3, pady=(10, 2))

        self.safe_switch = ctk.CTkSwitch(left, text="Safe Mode (+1s while on)", font=FONT3,
                                         command=self.on_safe)
        self.safe_switch.grid(row=8, column=0, columnspan=3, pady=2)

        self.hint_label = ctk.CTkLabel(
            left, font=("Verdana", 11), text_color=GRAY, justify="left", wraplength=380,
            text="In Zoom/Discord choose  Mic: BlackHole 2ch (Mac) / CABLE Output (Windows)"
                 "  and  Camera: OBS Virtual Camera.  Don't pick OBS Virtual Camera above."
                 "  Windows: pick OBS Virtual Camera in Zoom/Discord BEFORE Start.")
        self.hint_label.grid(row=9, column=0, columnspan=3, padx=10, pady=(10, 0))

        # ---- right: live view
        right = ctk.CTkFrame(main, width=(WIDTH - 40) / 2 - 15, height=HEIGHT - 170)
        right.grid(row=0, column=1, padx=(5, 10), pady=10)
        right.pack_propagate(False)
        ctk.CTkLabel(right, text="What others see", font=FONT2).pack(pady=(10, 6))

        from PIL import Image
        self._blank = Image.new("RGB", (PREVIEW_W, PREVIEW_H), (217, 217, 217))
        # ONE image for the whole app life; we only swap its picture (never image=None:
        # CustomTkinter then keeps pointing at a deleted Tk image -> "pyimageN doesn't exist")
        self._preview_img = ctk.CTkImage(light_image=self._blank, dark_image=self._blank,
                                         size=(PREVIEW_W, PREVIEW_H))
        self.preview_label = ctk.CTkLabel(right, text="Not running", image=self._preview_img,
                                          compound="center", width=PREVIEW_W, height=PREVIEW_H)
        self.preview_label.pack()

        self.state_label = ctk.CTkLabel(right, text="● STOPPED", font=FONT2, text_color=GRAY)
        self.state_label.pack(pady=(8, 0))
        self.stats_label = ctk.CTkLabel(right, text="", font=FONT3, text_color=GRAY)
        self.stats_label.pack()

        self.unsay_button = ctk.CTkButton(right, text="UNSAY\n(tap, or hold to stay hidden)",
                                          font=FONT2, height=64, width=300,
                                          fg_color=RED, hover_color="#8e0000")
        self.unsay_button.pack(pady=10)
        self.unsay_button.bind("<ButtonPress-1>", lambda e: self.mic and self.mic.unsay_press())
        self.unsay_button.bind("<ButtonRelease-1>", lambda e: self.mic and self.mic.unsay_release())

        # ---- bottom buttons
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(pady=4)
        self.start_button = ctk.CTkButton(bottom, text="Start", width=140, command=self.toggle_start,
                                          fg_color=GREEN, hover_color="#1b5e20")
        self.start_button.grid(row=0, column=0)
        self.preview_button = ctk.CTkButton(bottom, text="Hide Preview", width=140,
                                            command=self.toggle_preview)
        self.preview_button.grid(row=0, column=1, padx=10)
        ctk.CTkButton(bottom, text="Save Settings", width=140,
                      command=self.save_settings).grid(row=0, column=2)

    # ------------------------------------------------------------ devices --
    def _load_devices(self):
        try:
            mics = [SYSTEM_DEFAULT] + [name for _, name in list_microphones()]
            outs = [name for _, name in list_virtual_outputs()] or [NO_CABLE]
        except Exception as e:  # PortAudio missing etc.
            mics, outs = [SYSTEM_DEFAULT], [NO_CABLE]
            error_message("Audio", f"Could not list audio devices:\n{e}")
        self.mic_menu.configure(values=mics)
        self.out_menu.configure(values=outs)

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
        messagebox.showinfo("Cameras", f"Working cameras: {', '.join(found) or 'none'}\n\n"
                                       "One of them is OBS Virtual Camera itself: don't pick that one "
                                       "(your video would loop). Try each and look at the preview.")

    # ----------------------------------------------------------- settings --
    def _apply_settings_to_widgets(self):
        s = self.settings
        if s.get("name"):
            self.name_entry.insert(0, s["name"])
        mics = self.mic_menu.cget("values")
        self.mic_menu.set(s["audio"]["audio_in"] if s["audio"]["audio_in"] in mics else SYSTEM_DEFAULT)
        outs = self.out_menu.cget("values")
        wanted = s["audio"]["audio_out"] or default_virtual_output_safe()
        self.out_menu.set(wanted if wanted in outs else outs[0])
        cam = str(s["video"]["camera"]) if s["video"]["enabled"] else CAMERA_OFF
        values = list(self.cam_menu.cget("values"))
        if cam not in values:
            self.cam_menu.configure(values=values + [cam])
        self.cam_menu.set(cam)
        delay = float(s["controls"]["delay"])
        self.delay_slider.set(delay)
        self.delay_label.configure(text=f"{delay:.1f}s")
        self.key_entry.insert(0, s["controls"]["undo_key"])
        if s["video"]["badge"]:
            self.badge_switch.select()
        else:
            self.badge_switch.deselect()

    def _read_widgets(self):
        s = self.settings
        s["name"] = self.name_entry.get().strip()
        mic = self.mic_menu.get()
        s["audio"]["audio_in"] = None if mic == SYSTEM_DEFAULT else mic
        out = self.out_menu.get()
        s["audio"]["audio_out"] = None if out.startswith("(") else out
        cam = self.cam_menu.get()
        s["video"]["enabled"] = cam.isdigit()
        if cam.isdigit():
            s["video"]["camera"] = int(cam)
        s["video"]["badge"] = bool(self.badge_switch.get())
        s["controls"]["delay"] = round(float(self.delay_slider.get()), 1)
        s["controls"]["undo_key"] = self.key_entry.get().strip() or "ctrl+shift+z"
        return s

    def save_settings(self):
        settings_manager.save_settings(self._read_widgets(), PROFILE)
        self.stats_label.configure(text="Settings saved")

    # ----------------------------------------------------------- controls --
    def toggle_start(self):
        if self.mic and self.mic.running:
            self.stop()
        else:
            self.start()

    def start(self):
        s = self._read_widgets()
        settings_manager.save_settings(s, PROFILE)
        if not s["audio"]["audio_out"]:
            error_message("Virtual microphone missing",
                          "No virtual audio cable found. Mac: brew install blackhole-2ch. Windows: install VB-Cable as administrator. Then restart the computer.")
            return
        try:
            parse_combo(s["controls"]["undo_key"])
        except ValueError as e:
            error_message("Unsay key", str(e))
            return

        self.mic = VirtualMicrophone(out_name=s["audio"]["audio_out"], in_name=s["audio"]["audio_in"],
                                     delay=s["controls"]["delay"])
        try:
            self.mic.start()
        except Exception as e:
            self.mic = None
            error_message("Microphone", f"Could not start the microphone:\n{e}")
            return

        self.cam = None
        if s["video"]["enabled"]:
            v = s["video"]
            self.cam = VirtualCamera(self.mic, camera=v["camera"], width=v["width"],
                                     height=v["height"], fps=v["fps"], badge=v["badge"])
            err = self.cam.start()
            if err:
                error_message("Camera", f"{err}\n\nAudio keeps working. Try another camera number.")

        try:
            self.mic.start_hotkeys(s["controls"]["undo_key"])
        except Exception as e:
            error_message("Unsay key",
                          f"The keyboard shortcut is not available:\n{e}\n\n"
                          "Use the UNSAY button instead. On macOS, allow your terminal in "
                          "Privacy & Security > Accessibility and Input Monitoring.")

        if self.safe_switch.get():
            self.mic.set_safe_mode(True)
        self.start_button.configure(text="Stop", fg_color=RED, hover_color="#8e0000")
        for w in (self.mic_menu, self.out_menu, self.cam_menu):
            w.configure(state="disabled")

    def stop(self):
        if self.cam:
            self.cam.stop()
        if self.mic:
            self.mic.stop()
        self.cam = None
        self.mic = None
        self.start_button.configure(text="Start", fg_color=GREEN, hover_color="#1b5e20")
        for w in (self.mic_menu, self.out_menu, self.cam_menu):
            w.configure(state="normal")
        self._show_placeholder("Not running")
        self.state_label.configure(text="● STOPPED", text_color=GRAY)
        self.stats_label.configure(text="")

    def on_delay(self, value):
        value = round(float(value), 1)
        self.delay_label.configure(text=f"{value:.1f}s")
        if self.mic and self.mic.running:
            self.mic.set_delay(value)

    def apply_key(self):
        key = self.key_entry.get().strip()
        try:
            parse_combo(key)
        except ValueError as e:
            error_message("Unsay key", str(e))
            return
        if self.mic and self.mic.running:
            try:
                self.mic.start_hotkeys(key)
            except Exception as e:
                error_message("Unsay key", str(e))
                return
        self.settings["controls"]["undo_key"] = key
        self.stats_label.configure(text=f"Unsay key set to {key.upper()}")

    def on_safe(self):
        if self.mic and self.mic.running:
            self.mic.set_safe_mode(bool(self.safe_switch.get()))

    def toggle_preview(self):
        self.preview_on = not self.preview_on
        self.preview_button.configure(text="Hide Preview" if self.preview_on else "Show Preview")
        if not self.preview_on:
            self._show_placeholder("Preview hidden")

    # ------------------------------------------------------------ refresh --
    def refresh(self):
        try:
            self._refresh_status()
            self._refresh_preview()
        finally:
            self.after(100, self.refresh)

    def _refresh_status(self):
        stats = self.mic.stats() if self.mic else None
        if not stats:
            self.state_label.configure(text="● STOPPED", text_color=GRAY)
            return
        if stats["holding"]:
            self.state_label.configure(text="● HIDDEN (holding)", text_color=RED)
        elif stats["muting"] or (self.cam and self.cam.frozen):
            self.state_label.configure(text="● UNSAID", text_color=ORANGE)
        else:
            self.state_label.configure(text="● LIVE", text_color=GREEN)
        talk = "talking" if stats["speaking"] else "quiet"
        safe = "  ·  SAFE MODE" if stats["safe_mode"] else ""
        text = f"Delay {stats['delay']:.1f}s  ·  {talk}  ·  {stats['undo_count']} unsays{safe}"
        if self.cam and self.cam.error:
            text += f"\nVideo: {self.cam.error}"
        if self.mic.hotkey_error:
            text += f"\nKey: {self.mic.hotkey_error} (use the button)"
        self.stats_label.configure(text=text)

    def _refresh_preview(self):
        if not (self.preview_on and self.cam and self.cam.preview is not None):
            if self.mic and self.mic.running and self.preview_on and not self.cam:
                if self.preview_label.cget("text") != "Audio only":
                    self._show_placeholder("Audio only")
            return
        import cv2
        from PIL import Image
        frame = cv2.resize(self.cam.preview, (PREVIEW_W, PREVIEW_H), interpolation=cv2.INTER_AREA)
        img = Image.fromarray(frame[:, :, ::-1].copy())      # BGR -> RGB
        self._preview_img.configure(light_image=img, dark_image=img)
        if self.preview_label.cget("text"):
            self.preview_label.configure(text="")

    def _show_placeholder(self, text):
        self._preview_img.configure(light_image=self._blank, dark_image=self._blank)
        self.preview_label.configure(text=text)

    def on_close(self):
        try:
            self.stop()
        finally:
            self.destroy()


def default_virtual_output_safe():
    try:
        return default_virtual_output()
    except Exception:
        return None


if __name__ == "__main__":
    import signal
    if hasattr(signal, "SIGTSTP"):          # Ctrl+Z in the terminal must not freeze the app
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    app = UnsayApp()
    app.mainloop()
