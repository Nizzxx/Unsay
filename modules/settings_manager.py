import json

DEFAULT_SETTINGS = {"video": {"camera": 0, "delay": 1, "width": 1280, "height": 720, "fps": 60, "device": None, "jpeg": False}, "audio": {"enabled": False, "audio_in": None, "audio_out": None, "audio_rate": 48000}, "controls": {"key": "G", "sign": "Left_hand"}}

def get_settings(cam_name:str):
    try:
        with open("settings.json", "r") as file:
            settings = json.load(file)
        file.close()
    except:
        settings = {}

    if not cam_name in settings:
        settings[cam_name] = DEFAULT_SETTINGS

    return settings[cam_name]

def save_settings(settings:dict, cam_name:str):
    try:
        with open("settings.json", "r") as file:
            settings_file = json.load(file)
        file.close()
    except:
        settings_file = {}

    settings_file[cam_name] = settings

    with open("settings.json", "w") as file:
        json.dump(settings_file, file)

    file.close()
