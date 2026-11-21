import json


def get_settings():
    with open("settings.json", "r+") as file:
        settings = json.load(file)

    delay = settings['video']['delay']
    key = settings['controls']['key']
    sign = settings['controls']['sign']

    return settings


CAMERA = 0
DELAY = 3
WIDTH = 1280
HEIGHT = 720
FPS = 30
DEVICE = None

# Audio
ENABLED = False
AUDIO_IN = None
AUDIO_OUT = None
AUDIO_RATE = 48000
