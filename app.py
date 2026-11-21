# Imports/utils
import customtkinter as ctk

# Constants
WIDTH = 700
HEIGHT = 400
FONT = ("Verdana", 24, "bold")

# Window parametrization
app = ctk.CTk()
app.title("Unsay")
app.minsize(WIDTH, HEIGHT)
app.maxsize(WIDTH, HEIGHT)

dashboard_label = ctk.CTkLabel(app, text="Dashboard", font=FONT)
dashboard_label.pack(pady=10)

main_frame = ctk.CTkFrame(app, width=WIDTH - 50, height=HEIGHT - 100)
main_frame.pack()

bottom_frame = ctk.CTkFrame(app)
bottom_frame.pack(pady=10)

start_button = ctk.CTkButton(bottom_frame, text="Start Camera")
start_button.grid(row=0, column=0)

preview_button = ctk.CTkButton(bottom_frame, text="Preview Camera")
preview_button.grid(row=0, column=1, padx=10)

update_button = ctk.CTkButton(bottom_frame, text="Update Camera")
update_button.grid(row=0, column=2)

# Run App !
app.mainloop()
