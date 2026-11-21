from modules import settings_manager, camera

class VirtualCamera:

    def __init__(self, cam_num=0):
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

cam = VirtualCamera()
#cam.start()
