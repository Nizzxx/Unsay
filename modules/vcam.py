import settings_manager
import camera

class VirtualCamera:

    def init(self, cam_num=0):
        self.cam_name = 'cam' + str(cam_num)
        self.settings = settings_manager.get_settings(self.cam_name)

        self.delay = self.settings['video']['delay']
        self.key = self.settings['controls']['key']
        self.sign = self.settings['controls']['sign']

        self.width = self.settings['video']['width']
        self.height = self.settings['video']['height']
        self.fps = self.settings['video']['fps']

    def start(self):
        camera.launch_virt_camera(self.width, self.height, self.fps, self.delay, self.key, self.sign)

    def modify_settings(self):
        self.settings['video']['delay'] = self.delay
        self.settings['controls']['key'] = self.key
        self.settings['controls']['sign'] = self.sign

        self.settings['video']['width'] = self.width
        self.settings['video']['height'] = self.height
        self.settings['video']['fps'] = self.fps

        settings_manager.save_settings(self.settings, self.cam_name)

cam = VirtualCamera(1)
cam.fps = 60
cam.modify_settings()
cam.start()
