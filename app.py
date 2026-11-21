# Imports/utils
import json
import customtkinter as ctk
from tkinter import messagebox
from modules.vcam import VirtualCamera
import threading

# Set appearance mode
ctk.set_appearance_mode("light")

# Constants
WIDTH = 700
HEIGHT = 400
FONT1 = ("Verdana", 24, "bold")
FONT2 = ("Verdana", 16, "bold")

# Variables
cameras = []
cam_selected = VirtualCamera("Cam1")
is_selected = True
is_camera_running = False

# Display error
def error_message(title: str, message: str):
    messagebox.showerror(
        title=title,
        message=message
    )

# Get existing cameras
def load_cameras():
    pass

# Create camera frame, button, and other
def camera_frame(camera: VirtualCamera):
    pass

# Display camera options
def display_options(camera: VirtualCamera):
    slider_value = 5.0

    def update_slider_value(value):
        global slider_value
        slider_value = round(value, 1)
        slider_value_label.configure(text=f"{slider_value}s")

    name_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
    name_frame.pack()

    name_label = ctk.CTkLabel(name_frame, text="Name :", font=FONT2)
    name_label.grid(row=0, column=0, padx=10)

    name_entry = ctk.CTkEntry(name_frame, placeholder_text="...")
    name_entry.grid(row=0, column=1)

    delay_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
    delay_frame.pack(pady=10)

    delay_label = ctk.CTkLabel(delay_frame, text="Delay :", font=FONT2)
    delay_label.grid(row=0, column=0, padx=(10, 0))

    delay_slider = ctk.CTkSlider(
        delay_frame,
        from_=0,
        to=10,
        number_of_steps=100,
        command=update_slider_value
    )
    delay_slider.grid(row=0, column=1)

    slider_value_label = ctk.CTkLabel(delay_frame, text=f"{slider_value}s")
    slider_value_label.grid(row=0, column=2, padx=5)

# Start virtual camera
def start_camera(camera: VirtualCamera):
    global is_camera_running
    
    if not is_selected:
        error_message("Camera Error", "Error: No Camera Selected")
        return 0

    if is_camera_running:
        error_message("Camera Error", "A Camera is already running")
        return 0

    is_camera_running = True
    cam_thread = threading.Thread(target=cam_selected.start)
    cam_thread.start()

# Preview virtual camera
def preview_camera(camera: VirtualCamera):
    if not is_selected:
        error_message("Camera Error", "Error: No Camera Selected")
        return 0

# Update camera settings
def update_camera(camera: VirtualCamera):
    if not is_selected:
        error_message("Camera Error", "Error: No Camera Selected")
        return 0

# Window parametrization
app = ctk.CTk()
app.title("Unsay")
app.geometry(f"{WIDTH}x{HEIGHT}")
app.resizable(False, False)

dashboard_label = ctk.CTkLabel(app, text="Dashboard", font=FONT1)
dashboard_label.pack(pady=10)

main_frame = ctk.CTkFrame(app, width=WIDTH - 50, height=HEIGHT - 100)
main_frame.pack_propagate(False)
main_frame.pack()

# Virtual Cameras Frame
right_frame = ctk.CTkFrame(main_frame, width=(WIDTH - 50) / 2 - 20, height=HEIGHT - 120)
right_frame.grid(row=0, column=1, padx=(5, 10), pady=10)
right_frame.pack_propagate(False)

virtual_camera_label = ctk.CTkLabel(right_frame, text="Select Virtual Camera", font=FONT2)
virtual_camera_label.pack(pady=10)

# Camera Options Frame
left_frame = ctk.CTkFrame(main_frame, width=(WIDTH - 50) / 2 - 20, height=HEIGHT - 120)
left_frame.grid(row=0, column=0, padx=(10, 5), pady=10)
left_frame.pack_propagate(False)

camera_options_label = ctk.CTkLabel(left_frame, text="Camera Options", font=FONT2)
camera_options_label.pack(pady=10)

bottom_frame = ctk.CTkFrame(app, fg_color="transparent")
bottom_frame.pack(pady=10)

start_button = ctk.CTkButton(bottom_frame, text="Start Camera", command=lambda: start_camera(cam_selected))
start_button.grid(row=0, column=0)

preview_button = ctk.CTkButton(bottom_frame, text="Preview Camera", command=lambda: preview_camera(cam_selected))
preview_button.grid(row=0, column=1, padx=10)

update_button = ctk.CTkButton(bottom_frame, text="Update Camera", command=lambda: update_camera(cam_selected))
update_button.grid(row=0, column=2)

display_options(cam_selected)

# Run App !
app.mainloop()
