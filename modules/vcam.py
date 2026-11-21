import settings_manager
import camera


class VirtualCamera:

    def __init__(self):
        settings = settings_manager.get_settings()

        self.delay = settings['video']['delay']
        self.key = settings['controls']['key']
        self.sign = settings['controls']['sign']

        self.width = settings['video']['width']
        self.height = settings['video']['height']
        self.fps = settings['video']['fps']

    def start(self):
        camera.launch_virt_camera(self.width, self.height, self.fps, self.delay, self.key, self.sign)

cam = VirtualCamera()
cam.start()
