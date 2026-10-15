"""
相机管理模块 (ROS Topic 版本)
支持 3 个 Orbbec Dabai DC1 相机: 头顶 RGBD + 左手腕 + 右手腕

通过 ROS astra_camera 节点获取图像，订阅 color image_raw topic。
"""

import numpy as np
import threading
import time

import rospy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge


class CameraManager:
    """
    管理三个相机的采集 (通过 ROS Topic)。
    """

    def __init__(
        self,
        cam_high_topic="/camera_f/color/image_raw",
        cam_left_topic="/camera_l/color/image_raw",
        cam_right_topic="/camera_r/color/image_raw",
        width=640,
        height=480,
        fps=30,
    ):
        self.cam_high_topic = cam_high_topic
        self.cam_left_topic = cam_left_topic
        self.cam_right_topic = cam_right_topic
        self.width = width
        self.height = height
        self.fps = fps

        self._bridge = CvBridge()
        self._latest_high = None
        self._latest_left = None
        self._latest_right = None
        self._lock = threading.Lock()
        self._subscribers = []

    def start(self):
        """启动相机采集 (订阅 ROS topic)。"""
        # rospy.init_node 可能已被调用 (如 real_robot_inference 中)
        try:
            rospy.init_node("camera_manager", anonymous=True)
        except rospy.exceptions.ROSException:
            pass  # 已初始化

        topics = [
            (self.cam_high_topic, "high"),
            (self.cam_left_topic, "left"),
            (self.cam_right_topic, "right"),
        ]

        for topic, name in topics:
            sub = rospy.Subscriber(
                topic, Image,
                self._make_callback(name),
                queue_size=1,
                buff_size=2**24,
            )
            self._subscribers.append(sub)
            print(f"[Camera] 已订阅: {topic} ({name})")

        # 等待首帧到达
        timeout = time.time() + 10.0
        while time.time() < timeout:
            with self._lock:
                if self._latest_high is not None and self._latest_left is not None and self._latest_right is not None:
                    break
            time.sleep(0.1)

        with self._lock:
            missing = []
            if self._latest_high is None:
                missing.append(self.cam_high_topic)
            if self._latest_left is None:
                missing.append(self.cam_left_topic)
            if self._latest_right is None:
                missing.append(self.cam_right_topic)

        if missing:
            raise RuntimeError(
                f"等待相机超时，未收到帧: {missing}\n"
                f"请确认 astra_camera 节点已启动 (roslaunch astra_camera multi_camera.launch)"
            )

        print(f"[Camera] 已启动: high={self.cam_high_topic}, left={self.cam_left_topic}, right={self.cam_right_topic}")

    def _make_callback(self, name):
        """创建指定相机的回调函数。"""
        def callback(msg):
            frame = self._bridge.imgmsg_to_cv2(msg, "bgr8")
            h, w = frame.shape[:2]
            if (w, h) != (self.width, self.height):
                frame = cv2_resize(frame, (self.width, self.height))

            with self._lock:
                if name == "high":
                    self._latest_high = frame
                elif name == "left":
                    self._latest_left = frame
                elif name == "right":
                    self._latest_right = frame
        return callback

    def get_frames(self):
        """获取最新帧 (BGR, HWC, uint8)。"""
        with self._lock:
            return (
                self._latest_high.copy() if self._latest_high is not None else None,
                self._latest_left.copy() if self._latest_left is not None else None,
                self._latest_right.copy() if self._latest_right is not None else None,
            )

    def stop(self):
        """停止采集，注销 subscriber。"""
        for sub in self._subscribers:
            sub.unregister()
        self._subscribers.clear()
        print("[Camera] 已停止")


def cv2_resize(frame, target_size):
    """延迟导入 cv2 进行 resize，避免在非 ROS 环境中报错。"""
    import cv2
    return cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)


def format_obs_for_va(cam_high, cam_left, cam_right, joint_state, prompt):
    """
    将相机帧和关节状态格式化为 VA Server 期望的格式。

    VA Server 期望:
    - observation.images.cam_high: [H, W, 3] np.uint8
    - observation.images.cam_left_wrist: [H, W, 3] np.uint8
    - observation.images.cam_right_wrist: [H, W, 3] np.uint8
    - observation.state: [30] np.float32 (关节角度 + padding)
    - task: str (任务提示)
    """
    obs = {
        "observation.images.cam_high": cam_high,
        "observation.images.cam_left_wrist": cam_left,
        "observation.images.cam_right_wrist": cam_right,
        "observation.state": joint_state.astype(np.float32),
        "task": prompt,
    }
    return obs
