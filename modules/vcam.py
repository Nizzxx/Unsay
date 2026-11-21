import settings_manager
import camera


class VirtualCamera:

    def __init__(self, cam_num:int):
        self.settings = settings_manager.get_settings()
        self.cam_name = 'cam' + str(cam_num)
        self.delay = self.settings[self.cam_name]['video']['delay']
        self.key = self.settings[self.cam_name]['controls']['key']
        self.sign = self.settings[self.cam_name]['controls']['sign']

        self.width = self.settings[self.cam_name]['video']['width']
        self.height = self.settings[self.cam_name]['video']['height']
        self.fps = self.settings[self.cam_name]['video']['fps']

    def start(self):
        camera.launch_virt_camera(self.width, self.height, self.fps, self.delay, self.key, self.sign)

    def modify_settings(self):
        self.settings[self.cam_name]['video']['delay'] = self.delay
        self.settings[self.cam_name]['controls']['key'] = self.key
        self.settings[self.cam_name]['controls']['sign'] = self.sign

        self.settings[self.cam_name]['video']['width'] = self.width
        self.settings[self.cam_name]['video']['height'] = self.height
        self.settings[self.cam_name]['video']['fps'] = self.fps

        settings_manager.save_settings(self.settings)


cam = VirtualCamera(0)
cam.fps = 60
cam.modify_settings()
cam.start()
