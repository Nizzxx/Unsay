import json

def get_settings():
    with open("settings.json", "r") as file:
        settings = json.load(file)
    file.close()

    return settings

def save_settings(settings:dict):
    with open("settings.json", "w") as file:
        json.dump(settings, file)

    file.close()
