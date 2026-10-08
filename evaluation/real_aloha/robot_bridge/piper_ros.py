"""Adapted subscriber-only Piper backend; see LICENSE.reference/source-manifest.json."""

import threading
import time
import numpy as np
from scipy.spatial.transform import Rotation

DEFAULT_CAMERA_TOPICS = {
    "cam_high": "/camera_f/color/image_raw",
    "cam_left_wrist": "/camera_l/color/image_raw",
    "cam_right_wrist": "/camera_r/color/image_raw",
}
ARM_STATUS_NAMES = {
    0: "NORMAL",
    1: "EMERGENCY_STOP",
    2: "NO_SOLUTION",
    3: "SINGULARITY_POINT",
    4: "TARGET_POS_EXCEEDS_LIMIT",
    5: "JOINT_COMMUNICATION_ERR",
    6: "JOINT_BRAKE_NOT_RELEASED",
    7: "COLLISION_OCCURRED",
    8: "OVERSPEED_DURING_TEACHING_DRAG",
    9: "JOINT_STATUS_ERR",
    10: "OTHER_ERR",
    11: "TEACHING_RECORD",
    12: "TEACHING_EXECUTION",
    13: "TEACHING_PAUSE",
    14: "MAIN_CONTROLLER_NTC_OVER_TEMPERATURE",
    15: "RELEASE_RESISTOR_NTC_OVER_TEMPERATURE",
}


class PiperRosBackend:
    is_physical = True

    def __init__(
        self,
        *,
        allow_motion=False,
        base_transforms=None,
        stale_seconds=0.5,
        timeout=5.0,
        gripper_max_m=0.08,
        joint_tolerance=0.002,
        gripper_tolerance_m=0.001,
    ):
        if allow_motion:
            raise ValueError("This backend is read-only")
        self.allow_motion = allow_motion
        self.transforms = None
        if base_transforms is not None:
            matrices = np.asarray(base_transforms, float)
            if matrices.shape != (2, 4, 4) or not np.isfinite(matrices).all():
                raise ValueError(
                    "Expected two finite robot_base_from_arm_base 4x4 matrices"
                )
            for matrix in matrices:
                r = matrix[:3, :3]
                if (
                    not np.allclose(matrix[3], [0, 0, 0, 1])
                    or not np.allclose(r.T @ r, np.eye(3), atol=1e-05)
                    or (not np.isclose(np.linalg.det(r), 1, atol=1e-05))
                ):
                    raise ValueError("Base transforms must be rigid transforms")
            self.transforms = matrices
        self.frame = (
            "robot_base" if self.transforms is not None else "per_arm_base_uncalibrated"
        )
        for value in (
            stale_seconds,
            timeout,
            gripper_max_m,
            joint_tolerance,
            gripper_tolerance_m,
        ):
            if not np.isfinite(value) or value <= 0:
                raise ValueError(
                    "Timeouts, tolerances and gripper range must be positive"
                )
        self.stale_seconds, self.timeout = (stale_seconds, timeout)
        self.gripper_max_m = gripper_max_m
        self.joint_tolerance, self.gripper_tolerance_m = (
            joint_tolerance,
            gripper_tolerance_m,
        )
        self._lock = threading.RLock()
        self._samples = {}
        self._subscriptions = []
        self._ros = None
        self._fault = None

    def _receive(self, msg, key):
        with self._lock:
            self._samples[key] = (time.monotonic(), msg)

    def connect(self):
        import rospy
        from cv_bridge import CvBridge
        from geometry_msgs.msg import PoseStamped
        from sensor_msgs.msg import Image, JointState
        from piper_msgs.msg import PiperStatusMsg

        self._ros, self._bridge = (rospy, CvBridge())
        if not rospy.core.is_initialized():
            rospy.init_node("astra_piper_bridge", anonymous=True, disable_signals=True)
        for name, topic in DEFAULT_CAMERA_TOPICS.items():
            self._subscriptions.append(
                rospy.Subscriber(
                    topic,
                    Image,
                    self._receive,
                    callback_args=name,
                    queue_size=1,
                    buff_size=4 * 1024 * 1024,
                )
            )
        for arm in ("left", "right"):
            for key, topic, cls in (
                ("joints", f"/puppet/joint_{arm}", JointState),
                ("pose", f"/puppet/end_pose_{arm}", PoseStamped),
                ("status", f"/puppet/arm_status_{arm}", PiperStatusMsg),
            ):
                self._subscriptions.append(
                    rospy.Subscriber(
                        topic,
                        cls,
                        self._receive,
                        callback_args=f"{arm}_{key}",
                        queue_size=1,
                    )
                )

    def _sample(self, key, after=None, ros_after=None):
        with self._lock:
            item = self._samples.get(key)
        if item is None:
            raise RuntimeError(f"Missing ROS feedback: {key}")
        received, msg = item
        if time.monotonic() - received > self.stale_seconds:
            raise RuntimeError(f"Stale ROS feedback: {key}")
        if after is not None and received <= after:
            raise RuntimeError(f"Waiting for post-command feedback: {key}")
        if hasattr(msg, "header"):
            stamp = msg.header.stamp.to_sec()
            age = self._ros.Time.now().to_sec() - stamp
            if (
                not np.isfinite([stamp, age]).all()
                or stamp <= 0
                or age > self.stale_seconds
                or age < -0.1
            ):
                raise RuntimeError(f"Invalid/stale ROS timestamp: {key}")
            if ros_after is not None and stamp <= ros_after:
                raise RuntimeError(f"Waiting for post-command feedback: {key}")
        return msg

    def _check_status(self):
        if self._fault:
            raise RuntimeError(self._fault)
        for arm in ("left", "right"):
            status = self._sample(f"{arm}_status")
            if status.err_code or status.arm_status:
                name = ARM_STATUS_NAMES.get(
                    int(status.arm_status), f"0x{status.arm_status:02X}"
                )
                raise RuntimeError(
                    f"{arm} not safe to move: arm_status={name}, err_code={status.err_code}"
                )
            if any(
                (
                    getattr(status, f"joint_{i}_angle_limit", False)
                    or getattr(status, f"communication_status_joint_{i}", False)
                    for i in range(1, 7)
                )
            ):
                raise RuntimeError(f"{arm} joint limit or CAN communication fault")

    def get_state(self, after=None, ros_after=None):
        self._check_status()
        states, positions, quaternions = ([], [], [])
        for i, arm in enumerate(("left", "right")):
            joints = np.asarray(
                self._sample(f"{arm}_joints", after, ros_after).position, float
            )
            if joints.shape != (7,) or not np.isfinite(joints).all():
                raise RuntimeError(f"Invalid joint feedback: {arm}")
            if not -0.001 <= joints[6] <= self.gripper_max_m + 0.001:
                raise RuntimeError(f"Invalid measured gripper opening: {arm}")
            pose = self._sample(f"{arm}_pose", after, ros_after).pose
            p = np.array([pose.position.x, pose.position.y, pose.position.z])
            q = np.array(
                [
                    pose.orientation.x,
                    pose.orientation.y,
                    pose.orientation.z,
                    pose.orientation.w,
                ]
            )
            if not np.isfinite(np.r_[p, q]).all() or abs(np.linalg.norm(q) - 1) > 0.001:
                raise RuntimeError(f"Invalid EEF feedback: {arm}")
            if self.transforms is not None:
                t = self.transforms[i]
                p = t[:3, :3] @ p + t[:3, 3]
                q = (Rotation.from_matrix(t[:3, :3]) * Rotation.from_quat(q)).as_quat()
            states.extend([*joints[:6], np.clip(joints[6] / self.gripper_max_m, 0, 1)])
            positions.append(p)
            quaternions.append(q[[3, 0, 1, 2]])
        return (
            np.asarray(states, np.float32),
            np.asarray(positions, np.float32),
            np.asarray(quaternions, np.float32),
        )

    def get_cameras(self):
        frames = {}
        for name in DEFAULT_CAMERA_TOPICS:
            msg = self._sample(name)
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
            if frame.shape != (480, 640, 3) or frame.dtype != np.uint8:
                raise RuntimeError(
                    f"Expected 640x480 RGB camera: {name}, got {frame.shape}"
                )
            frames[name] = frame.copy()
        return frames

    def wait_ready(self):
        deadline, last = (time.monotonic() + self.timeout, None)
        while time.monotonic() < deadline and (not self._ros.is_shutdown()):
            try:
                self.get_state()
                self.get_cameras()
                return
            except RuntimeError as exc:
                last = exc
                time.sleep(0.05)
        raise RuntimeError(f"ROS observation not ready: {last}")

    def close(self):
        for sub in self._subscriptions:
            sub.unregister()
