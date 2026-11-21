import threading

from modules import settings_manager, camera

class VirtualCamera:

    def __init__(self, cam_name: str):
        self.cam_name = cam_name
        self.settings = settings_manager.get_settings(self.cam_name)

        self.delay = self.settings['video']['delay']
        self.key = self.settings['controls']['key']
        self.sign = self.settings['controls']['sign']

        self.width = self.settings['video']['width']
        self.height = self.settings['video']['height']
        self.fps = self.settings['video']['fps']

        # Set by stop(); camera.launch_virt_camera checks it to know when to exit
        self._stop_event = threading.Event()

    def start(self):
        self._stop_event.clear()
        camera.launch_virt_camera(
            self.width, self.height, self.fps, self.delay, self.key, self.sign,
            stop_event=self._stop_event
        )

    def stop(self):
        self._stop_event.set()

    def modify_settings(self):
        self.settings['video']['delay'] = self.delay
        self.settings['controls']['key'] = self.key
        self.settings['controls']['sign'] = self.sign

        self.settings['video']['width'] = self.width
        self.settings['video']['height'] = self.height
        self.settings['video']['fps'] = self.fps

        settings_manager.save_settings(self.settings, self.cam_name)
