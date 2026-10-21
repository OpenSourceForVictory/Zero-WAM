"""
Piper 客户端：Zero-WAM 16D EEF 经本地逆解检查后执行 MOVE_J
运行环境: piper_new

用法:
    python -m evaluation.real_aloha.run_piper_eef --prompt "Place bread on plates" --control_mode movejs --max_chunks 1

This entry retains the original hardware startup (including enable). It is not
a preview. For 16D actions, controller-backed local IK selects a nearby joint
target for MOVE_J; CPV remains supported only for the original joint actions.
This does not provide scene collision checking or verified stopping dynamics.
Source: user-provided lingbot-va-piper/client/real_robot_inference_merged.py.
"""

import argparse
import sys
from scipy.spatial.transform import Rotation as R
import json
import os
import shutil
import signal
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# Reuse the installed, previously used hardware controller/camera modules.
# Only this project's client owns Zero-WAM protocol and EEF action handling.
sys.path.insert(0, os.environ.get("LINGBOT_CLIENT_DIR", "/home/agilex/models/lingbot-va-piper/client"))
from coordinate_transform import compute_base_transform, transform_pose_euler
from tcp_utils import flange_to_tcp_pose, tcp_to_flange_pose
from piper_controller import PiperController
from camera_manager import CameraManager
from va_client import WebsocketClientPolicy as _SiteWebsocketClientPolicy
from realtime_visualizer import InferenceStatus
from joint_trajectory_smoother import JointTrajectorySmoother
from .piper_kinematic_guard import LivePiperGuard

os.environ["KINETO_LOG_LEVEL"] = "5"


class WebsocketClientPolicy(_SiteWebsocketClientPolicy):
    def close(self):
        """Disconnect without the site's legacy close() clearing operator stop."""
        if self._ws is not None:
            self._ws.close()
            self._ws = None


# ─────────────────────── 配置 ───────────────────────


@dataclass
class BaseConfig:
    """共享参数"""
    prompt: str = ""
    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 39056
    icl_id: str = "bread_plate_20261008_v1"
    no_transform: bool = False
    tcp_offset: float = 0.0
    # CAN
    can_left: str = "can_left"
    can_right: str = "can_right"
    firmware_version: str = "v188"
    # Camera
    cam_high: str = "/camera_f/color/image_raw"
    cam_left: str = "/camera_l/color/image_raw"
    cam_right: str = "/camera_r/color/image_raw"
    cam_width: int = 640
    cam_height: int = 480
    cam_fps: int = 30
    # Execution
    max_chunks: int = 50
    max_eef_actions: int = 0  # 0 means complete chunks; positive limits the entire run.
    eef_step_limit: float = 0.0
    eef_rotation_limit: float = 0.0
    hold_grippers: bool = False
    save_dir: str = "./inference_logs"
    wait_tol_deg: float = 3.0
    # Smoothing
    smooth_method: str = "spline"
    smooth_factor: float = 0.1
    v_max: float = 3.0
    outlier_threshold: float = 10.0
    savgol_window: int = 11
    savgol_order: int = 2
    butter_cutoff: float = 5.0

@dataclass
class MoveJConfigOrigin(BaseConfig):
    """MOVE_J / MOVE_JS 模式配置"""
    control_mode: str = "movej"
    action_dt: float = 0.02
    movej_speed_percent: float = 0.15
    v_max: float = 0.5
    wait_tol_deg: float = 3.0
    # Smoothing
    smooth_method: str = "none"
    smooth_factor: float = 0.1
    v_max: float = 3.0
    outlier_threshold: float = 10.0
    savgol_window: int = 11
    savgol_order: int = 2
    butter_cutoff: float = 5.0

@dataclass
class MoveJConfig(BaseConfig):
    """MOVE_J / MOVE_JS 模式配置"""
    control_mode: str = "movej"
    action_dt: float = 0.02
    movej_speed_percent: float = 0.06
    v_max: float = 0.5
    wait_tol_deg: float = 3.0
    # Smoothing
    smooth_method: str = "spline"
    smooth_factor: float = 0.1
    v_max: float = 3.0
    outlier_threshold: float = 10.0
    savgol_window: int = 11
    savgol_order: int = 2
    butter_cutoff: float = 5.0

@dataclass
class CPVConfig(BaseConfig):
    """CPV 模式配置"""
    control_mode: str = "cpv"
    action_dt: float = 0.05
    cpv_acc: float = 0.5
    cpv_dcc: float = 0.5
    cpv_cv: float = 0.5
    v_max: float = 0.3
    wait_tol_deg: float = 3.0
    # Smoothing
    smooth_method: str = "spline"
    smooth_factor: float = 0.1
    v_max: float = 3.0
    outlier_threshold: float = 10.0
    savgol_window: int = 11
    savgol_order: int = 2
    butter_cutoff: float = 5.0

@dataclass
class MoveJSConfig(BaseConfig):
    """MOVE_J / MOVE_JS 模式配置"""
    control_mode: str = "movejs"
    action_dt: float = 0.02
    movej_speed_percent: float = 0.1
    v_max: float = 0.5
    wait_tol_deg: float = 3.0
    # Smoothing
    smooth_method: str = "savgol"
    outlier_threshold: float = 10.0
    savgol_window: int = 11
    savgol_order: int = 3

CONFIG_MAP = {
    "origin": MoveJConfigOrigin,
    "movej": MoveJConfig,
    "movejs": MoveJSConfig,
    "cpv": CPVConfig,
}

# ─────────────────────── Logger ───────────────────────


class InferenceLogger:
    """推理过程日志记录器: 计时、进度、关键帧保存"""

    def __init__(self, save_dir: str, prompt: str, va_client=None):
        self.save_dir = Path(save_dir)
        self.prompt = prompt
        self.va_client = va_client
        self.start_time = None

        self.timings = {
            "server_infer_s": [],
            "action_exec_s": [],
            "kv_cache_s": [],
            "total_s": 0,
        }
        self.action_joint_list = []
        self.video_writers = None
        self.frame_h = self.frame_w = None

    def start(self, control_mode: str):
        self.start_time = time.time()
        self.save_dir.mkdir(parents=True, exist_ok=True)
        with open(self.save_dir / "task_info.json", "w") as f:
            json.dump({
                "prompt": self.prompt,
                "start_time": datetime.now().isoformat(),
                "action_space": f"joint_14_{control_mode}",
            }, f, indent=2)
        print(f"  [Logger] 日志保存目录: {self.save_dir}")

    def _init_video_writers(self, h, w):
        if self.video_writers is not None:
            return
        self.frame_h, self.frame_w = h, w
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.video_writers = {
            "high": cv2.VideoWriter(
                str(self.save_dir / "cam_high.mp4"), fourcc, 20.0, (w, h)),
            "left": cv2.VideoWriter(
                str(self.save_dir / "cam_left.mp4"), fourcc, 20.0, (w, h)),
            "right": cv2.VideoWriter(
                str(self.save_dir / "cam_right.mp4"), fourcc, 20.0, (w, h)),
            "combined": cv2.VideoWriter(
                str(self.save_dir / "combined.mp4"), fourcc, 20.0, (w * 3, h)),
        }

    def log_key_frame(self, chunk_idx, step_idx, cam_high, cam_left, cam_right, action):
        if cam_high is None:
            return
        if action is not None and len(action) >= 14:
            joints = np.array(
                [action[0], action[1], action[2], action[3], action[4], action[5],
                 action[7], action[8], action[9], action[10], action[11], action[12]],
                dtype=np.float64)
            self.action_joint_list.append(joints)

        h, w = cam_high.shape[:2]
        self._init_video_writers(h, w)
        self.video_writers["high"].write(cam_high)
        self.video_writers["left"].write(cam_left)
        self.video_writers["right"].write(cam_right)
        combined = np.hstack([cam_high, cam_left, cam_right])
        self.video_writers["combined"].write(combined)

    def log_server_infer(self, elapsed_s: float):
        self.timings["server_infer_s"].append(elapsed_s)

    def log_kv_cache(self, elapsed_s: float):
        self.timings["kv_cache_s"].append(elapsed_s)

    def finish(self, total_steps: int, success: bool = True, error: str = None):
        self.timings["total_s"] = time.time() - self.start_time
        self.timings["total_steps"] = total_steps
        self.timings["success"] = success
        self.timings["error"] = error

        if self.timings["server_infer_s"]:
            self.timings["server_infer_mean_s"] = float(np.mean(self.timings["server_infer_s"]))
            self.timings["server_infer_std_s"] = float(np.std(self.timings["server_infer_s"]))
        if self.timings["kv_cache_s"]:
            self.timings["kv_cache_mean_s"] = float(np.mean(self.timings["kv_cache_s"]))

        with open(self.save_dir / "timings.json", "w") as f:
            json.dump(self.timings, f, indent=2)

        if self.action_joint_list:
            arr = np.array(self.action_joint_list, dtype=np.float64)
            np.save(self.save_dir / "action_joints.npy", arr)
            print(f"  [Logger] 已保存 action_joints.npy: shape={arr.shape}")

        self._decode_and_copy_predicted_video()

        if self.video_writers:
            for vw in self.video_writers.values():
                vw.release()

        print(f"  [Logger] 已保存到 {self.save_dir}")
        print(f"  [Logger] 总耗时: {self.timings['total_s']:.1f}s, "
              f"Server推理: {self.timings.get('server_infer_mean_s', 0):.2f}s/次, "
              f"总步数: {total_steps}")

    def _decode_and_copy_predicted_video(self):
        if self.va_client is None:
            return
        try:
            print("  [Logger] 请求 server 解码 predicted video...", flush=True)
            ret = self.va_client.decode_latents()
            video_path = ret.get("video_path")
            if video_path and os.path.exists(video_path):
                dest = self.save_dir / "predicted_video.mp4"
                shutil.copy2(video_path, dest)
                print(f"  [Logger] predicted_video.mp4 已保存: {dest}")
            else:
                print(f"  [Logger] server 未生成 predicted video")
        except Exception as e:
            print(f"  [Logger] 解码 predicted video 失败: {e}")


# ─────────────────────── Joint 动作处理 ───────────────────────


def model_joint_action_to_piper(action_14: np.ndarray):
    """将模型输出的 14 维绝对 joint 动作转换为 Piper 指令格式.

    输入: action_14 [14]
        [left_j1..j6, left_gripper, right_j1..j6, right_gripper]
    单位: 弧度 (关节角), 米 (夹爪开口)
    """
    left_joints = [float(x) for x in action_14[0:6]]
    left_gripper = float(np.clip(action_14[6], 0, 0.1))
    right_joints = [float(x) for x in action_14[7:13]]
    right_gripper = float(np.clip(action_14[13], 0, 0.1))
    return left_joints, right_joints, left_gripper, right_gripper


def format_obs(cam_high, cam_left, cam_right, joint_state, prompt):
    """将相机帧和关节状态格式化为 VA Server 期望的格式."""
    cam_high = cv2.cvtColor(cam_high, cv2.COLOR_BGR2RGB)
    cam_left = cv2.cvtColor(cam_left, cv2.COLOR_BGR2RGB)
    cam_right = cv2.cvtColor(cam_right, cv2.COLOR_BGR2RGB)
    state = np.zeros(30, dtype=np.float32)
    n_joints = min(len(joint_state), 30)
    state[:n_joints] = joint_state[:n_joints].astype(np.float32)
    return {
        "observation.images.cam_high": cam_high,
        "observation.images.cam_left_wrist": cam_left,
        "observation.images.cam_right_wrist": cam_right,
        "observation.state": state,
        "task": prompt,
    }


# ─────────────────────── 模式切换 ───────────────────────


def restore_movej_mode(robot):
    """从 CPV 模式切回 MOVE_J 模式。"""
    robot.set_auto_set_motion_mode_enabled(True)
    robot.set_motion_mode('j')


def setup_cpv_mode(robot, acc, dcc, cv):
    """将机械臂切换到 CPV 模式并配置参数。"""
    robot.set_auto_set_motion_mode_enabled(False)
    robot.set_motion_mode('cpv')
    for j in range(1, 7):
        if acc > 0:
            robot.set_cpv_acc(j, acc)
        if dcc > 0:
            robot.set_cpv_dcc(j, dcc)
        if cv > 0:
            robot.set_cpv_cv(j, cv)


def send_cpv_joints(robot, joints):
    """向单臂发送 6 个关节的 CPV 位置指令。"""
    for i, pos in enumerate(joints, start=1):
        robot.move_cpv_pos(i, pos)


def send_cpv_joints_both(piper, left_joints, right_joints,
                         left_gripper, right_gripper):
    """双臂 CPV 位置指令 + 夹爪。"""
    send_cpv_joints(piper.robot_l, left_joints)
    send_cpv_joints(piper.robot_r, right_joints)
    left_gripper = max(0.0, min(0.08, left_gripper))
    right_gripper = max(0.0, min(0.08, right_gripper))
    piper.gripper_l.move_gripper_m(left_gripper, force=1.0)
    piper.gripper_r.move_gripper_m(right_gripper, force=1.0)


def send_action(piper, left_joints, right_joints, left_g, right_g,
                control_mode, first):
    """根据 control_mode 分发动作指令。"""
    if control_mode == "cpv":
        send_cpv_joints_both(piper, left_joints, right_joints, left_g, right_g)
    elif control_mode == "movejs":
        if first:
            piper.send_joints_with_gripper(
                left_joints, right_joints, left_g, right_g)
        else:
            piper.send_joints_with_gripper_movejs(
                left_joints, right_joints, left_g, right_g)
    else:
        piper.send_joints_with_gripper(
            left_joints, right_joints, left_g, right_g)


# ─────────────────────── 主推理循环 ───────────────────────


def run_inference(cfg):
    """执行完整的推理循环。"""

    mode = cfg.control_mode
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = str(Path(cfg.save_dir) / f"{timestamp}_{cfg.prompt.replace(' ', '_')[:20]}")
    logger = InferenceLogger(save_dir, cfg.prompt)

    print("=" * 60)
    print(f"  Piper 双臂 — LingBot VA 实时推理 ({mode.upper()} 模式)")
    print("=" * 60)

    # ── 1. 初始化 Piper 控制器 ──
    print("\n[1/4] 连接机械臂...")
    piper = PiperController(can_left=cfg.can_left, can_right=cfg.can_right,
                            firmware_version=cfg.firmware_version)
    try:
        piper.connect()
        if _check_emergency_stop():
            raise RuntimeError('Operator stop is set; refusing to enable')
        checked_eef_feedback(piper)
        piper.eef_guard = LivePiperGuard(piper)
        piper.eef_guard.check_start(piper)
        piper.enable()
    except Exception as e:
        print(f"[错误] 机械臂连接/使能失败: {e}")
        traceback.print_exc()
        piper.close()
        return

    # ── 模式初始化 ──
    if mode in ("movej", "movejs"):
        piper.robot_l.set_speed_percent(int(cfg.movej_speed_percent * 100))
        piper.robot_r.set_speed_percent(int(cfg.movej_speed_percent * 100))
        restore_movej_mode(piper.robot_l)
        restore_movej_mode(piper.robot_r)
    else:  # cpv
        print("  切换 CPV 模式...")
        try:
            setup_cpv_mode(piper.robot_l, acc=cfg.cpv_acc, dcc=cfg.cpv_dcc, cv=cfg.cpv_cv)
            setup_cpv_mode(piper.robot_r, acc=cfg.cpv_acc, dcc=cfg.cpv_dcc, cv=cfg.cpv_cv)
            ja = piper.get_joint_angles()
            send_cpv_joints(piper.robot_l, ja[:6])
            send_cpv_joints(piper.robot_r, ja[6:12])
            time.sleep(0.2)
            print(f"  CPV 参数: acc={cfg.cpv_acc} dcc={cfg.cpv_dcc} cv={cfg.cpv_cv} rad/s")
        except Exception as e:
            print(f"[错误] CPV 模式切换失败: {e}")
            traceback.print_exc()
            piper.close()
            return

    # ── 2. 初始化相机 ──
    print("\n[2/4] 启动相机...")
    camera = CameraManager(
        cam_high_topic=cfg.cam_high,
        cam_left_topic=cfg.cam_left,
        cam_right_topic=cfg.cam_right,
        width=cfg.cam_width,
        height=cfg.cam_height,
        fps=cfg.cam_fps,
    )
    try:
        camera.start()
    except Exception as e:
        print(f"[错误] 相机启动失败: {e}")
        if mode == "cpv":
            restore_movej_mode(piper.robot_l)
            restore_movej_mode(piper.robot_r)
        piper.close()
        return

    # ── 3. 连接 VA Server ──
    print(f"\n[3/4] 连接 VA Server ({cfg.server_host}:{cfg.server_port})...")
    client = WebsocketClientPolicy(host=cfg.server_host, port=cfg.server_port)
    try:
        metadata = client.connect()
        print(f"  Server metadata: {metadata}")
    except Exception as e:
        print(f"[错误] 无法连接 VA Server: {e}")
        camera.stop()
        if mode == "cpv":
            restore_movej_mode(piper.robot_l)
            restore_movej_mode(piper.robot_r)
        piper.close()
        return

    logger.va_client = client

    # ── 初始化轨迹平滑器 ──
    if cfg.smooth_method != "none":
        smoother = JointTrajectorySmoother(
            smooth_method=cfg.smooth_method,
            smooth_factor=cfg.smooth_factor,
            v_max=cfg.v_max,
            dt=cfg.action_dt,
            outlier_threshold_deg=cfg.outlier_threshold,
            savgol_window=cfg.savgol_window,
            savgol_order=cfg.savgol_order,
            butter_cutoff=cfg.butter_cutoff,
        )
        print(f"  平滑: {cfg.smooth_method}, factor={cfg.smooth_factor}, "
              f"v_max={cfg.v_max}rad/s, outlier_threshold={cfg.outlier_threshold}°")
    else:
        smoother = None

    # ── 4. 执行推理 ──
    print(f"\n[4/4] 开始推理...")
    print(f"  任务: {cfg.prompt}")
    print(f"  最大 chunk 数: {cfg.max_chunks}")
    print(f"  发送频率: {1/cfg.action_dt:.1f}Hz (dt={cfg.action_dt}s)")
    logger.start(mode)

    total_steps = 0
    error_msg = None
    cleanup_done = False

    def _cleanup(signum, frame):
        nonlocal error_msg, cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        error_msg = f"SignalInterrupt({signum})"
        print(f"\n[信号中断] 收到信号 {signum}")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    try:
        total_steps = _inference_loop(piper, camera, client, cfg, logger, smoother)
    except KeyboardInterrupt:
        print("\n[用户中断] Ctrl+C 收到")
        error_msg = "KeyboardInterrupt"
    except Exception as e:
        print(f"\n[错误] 推理过程中出错: {e}")
        traceback.print_exc()
        error_msg = str(e)
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        cleanup_done = True
        if mode == "cpv":
            print("\n正在切回 MOVE_J 模式...")
            try:
                restore_movej_mode(piper.robot_l)
                restore_movej_mode(piper.robot_r)
            except Exception:
                pass
        #
        # logger.finish(total_steps, success=(error_msg is None), error=error_msg)
        print("\n正在安全关闭...")
        client.close()
        camera.stop()
        piper.close()
        print("已关闭")





def _check_emergency_stop():
    _RUNTIME_CONFIG_PATH = "/home/agilex/models/lingbot-va-piper/client/runtime_config.json"
    """读取 runtime_config.json，如果 emergency_stop=True 则抛出异常。"""
    try:
        with open(_RUNTIME_CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        if cfg.get("emergency_stop", False):
            return True
        return False
    except (FileNotFoundError, json.JSONDecodeError):
        return False



# Zero-WAM's 16D path uses the legacy EEF convention, not the joint smoother.
def eef_initial(piper, transform, tcp_offset):
    poses = [np.asarray(x, float) for x in piper.get_end_pose()]
    result = []
    for i, pose in enumerate(poses):
        if pose.shape != (6,) or not np.isfinite(pose).all():
            raise ValueError("Invalid measured end pose")
        if tcp_offset:
            pose = flange_to_tcp_pose(pose, tcp_offset)
        if i == 1 and transform is not None:
            pose = transform_pose_euler(transform, pose)
        result.extend([*pose[:3], *R.from_euler('xyz', pose[3:]).as_quat(), 0.0])
    return np.asarray(result)


def relative_eef_to_piper(action, initial, transform=None, tcp_offset=0.0):
    action = np.asarray(action, float)
    if action.shape != (16,) or not np.isfinite(action).all():
        raise ValueError("Expected one finite 16D EEF action")
    poses, grippers = [], []
    for i in (0, 8):
        rotation = R.from_quat(initial[i+3:i+7]) * R.from_quat(action[i+3:i+7])
        pose = np.r_[initial[i:i+3]+action[i:i+3], rotation.as_euler('xyz')]
        if i == 8 and transform is not None:
            pose = transform_pose_euler(np.linalg.inv(transform), pose)
        if tcp_offset:
            pose = tcp_to_flange_pose(pose, tcp_offset)
        poses.append(pose)
        # Zero-WAM openings are normalized; the existing controller takes meters.
        grippers.append(float(np.clip(action[i+7], 0, 1))*0.08)
    return (*poses, *grippers)


def checked_eef_feedback(piper, after=None):
    """Read actual SDK samples; never accept the wrapper's missing-data zeros."""
    poses, stamps = [], []
    for name, arm in (("left", piper.robot_l), ("right", piper.robot_r)):
        status, pose = arm.get_arm_status(), arm.get_flange_pose()
        if status is None or pose is None:
            raise RuntimeError(f"{name}: missing EEF/status feedback")
        code = getattr(status.msg.arm_status, 'value', status.msg.arm_status)
        if code != 0:
            raise RuntimeError(f"{name}: controller arm_status={code}: {status.msg.arm_status}")
        control = getattr(status.msg, 'ctrl_mode', 1)
        control = getattr(control, 'value', control)
        if control != 1:
            raise RuntimeError(f"{name}: controller ctrl_mode={control}; require CAN_CTRL, end teaching locally")
        for sample in (status, pose):
            age = time.time() - sample.timestamp
            if not np.isfinite(age) or not -.1 <= age <= .5:
                raise RuntimeError(f"{name}: stale EEF/status feedback")
            stamps.append(sample.timestamp)
        values = np.asarray(pose.msg, float)
        if values.shape != (6,) or not np.isfinite(values).all():
            raise RuntimeError(f"{name}: invalid flange pose")
        poses.append(values)
    if after is not None and min(stamps) <= after:
        return None
    return np.asarray(poses)


def limit_eef_command(command, current, max_translation=0.0, max_rotation=0.0):
    """Explicit commissioning limits in per-arm flange frames (m/rad)."""
    poses = np.array(command[:2], dtype=float, copy=True)
    current = np.asarray(current, dtype=float)
    if poses.shape != (2, 6) or current.shape != (2, 6) or not np.isfinite([poses, current]).all():
        raise ValueError('Expected finite dual-arm flange poses')
    for i in range(2):
        delta = poses[i, :3] - current[i, :3]
        length = np.linalg.norm(delta)
        if max_translation > 0 and length > max_translation:
            poses[i, :3] = current[i, :3] + delta * (max_translation / length)
        start = R.from_euler('xyz', current[i, 3:])
        relative = (start.inv() * R.from_euler('xyz', poses[i, 3:])).as_rotvec()
        angle = np.linalg.norm(relative)
        if max_rotation > 0 and angle > max_rotation:
            poses[i, 3:] = (start * R.from_rotvec(relative * max_rotation / angle)).as_euler('xyz')
    return (*poses, *command[2:])


def send_eef_checked(piper, command, record_path, timeout=3.0, hold_grippers=False):
    """Count a step only after fresh feedback confirms the flange target."""
    row = dict(target_flange=np.asarray(command[:2]).tolist(),
               grippers_m=None if hold_grippers else list(command[2:]),
               gripper_commands=0, method='local_ik_move_j', sent=False, pose_reached=False)
    try:
        row['before_flange'] = checked_eef_feedback(piper).tolist()
        guard = getattr(piper, 'eef_guard', None)
        if guard is None:
            raise RuntimeError('Missing controller-backed kinematic guard; no command sent')
        row['kinematic_preflight'] = guard.validate(piper, row['before_flange'], command[:2])
        latest = checked_eef_feedback(piper)
        previous = np.asarray(row['before_flange'])
        if (np.any(np.linalg.norm(latest[:, :3]-previous[:, :3],axis=1) > .001)
                or np.any((R.from_euler('xyz',latest[:,3:]) *
                    R.from_euler('xyz',previous[:,3:]).inv()).magnitude() > .01)):
            raise RuntimeError('Robot moved during kinematic preflight; no command sent')
        if _check_emergency_stop():
            raise RuntimeError('Stop requested before EEF command')
        joints = np.asarray([row['kinematic_preflight'][name]['target_joints']
                             for name in ('left','right')],float)
        if joints.shape != (2,6) or not np.isfinite(joints).all():
            raise RuntimeError('Invalid checked joint targets')
        sent_at = time.time()
        row['sent_at'] = sent_at
        row['sent'] = True  # A two-arm send may partially succeed before raising.
        row['target_joints'] = joints.tolist()
        if hold_grippers:
            piper.robot_l.move_j(joints[0].tolist())
            piper.robot_r.move_j(joints[1].tolist())
        else:
            row['gripper_commands'] = 2
            piper.send_joints_with_gripper(joints[0].tolist(),joints[1].tolist(),*command[2:])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _check_emergency_stop():
                raise RuntimeError('Stop requested while awaiting EEF target')
            actual = checked_eef_feedback(piper, after=sent_at)
            if actual is not None:
                joint_samples=[arm.get_joint_angles() for arm in (piper.robot_l,piper.robot_r)]
                if any(s is None or not -.1 <= time.time()-s.timestamp <= .5 for s in joint_samples):
                    raise RuntimeError('Missing/stale joints during planned motion')
                actual_q=np.asarray([s.msg for s in joint_samples],float)
                if actual_q.shape!=(2,6) or not np.isfinite(actual_q).all():
                    raise RuntimeError('Invalid joints during planned motion')
                starts=np.asarray([row['kinematic_preflight'][name]['joint_path'][0]
                                   for name in ('left','right')])
                if np.any(actual_q < np.minimum(starts,joints)-.02) or np.any(actual_q > np.maximum(starts,joints)+.02):
                    raise RuntimeError('Joint feedback left checked motion corridor')
                joint_error=np.max(np.abs(actual_q-joints),axis=1)
                row.update(actual_joints=actual_q.tolist(),joint_error_rad=joint_error.tolist())
                row['actual_flange'] = actual.tolist()
                target = np.asarray(command[:2])
                distance = np.linalg.norm(actual[:, :3]-target[:, :3], axis=1)
                angle = (R.from_euler('xyz', actual[:, 3:]) *
                         R.from_euler('xyz', target[:, 3:]).inv()).magnitude()
                row.update(position_error_m=distance.tolist(), rotation_error_rad=angle.tolist())
                if (np.all(distance <= .002) and np.all(angle <= .02)
                        and np.all(joint_error <= .01)
                        and all(s.timestamp > sent_at for s in joint_samples)):
                    row['pose_reached'] = True
                    return
            time.sleep(.02)
        raise TimeoutError('EEF target not reached within timeout')
    except BaseException as exc:
        row['error'] = type(exc).__name__ + ': ' + str(exc)
        if row['sent']:
            # Stop without the legacy emergency_stop wrapper's automatic reset.
            for arm in (piper.robot_l, piper.robot_r):
                try:
                    arm.electronic_emergency_stop()
                except Exception as stop_error:
                    row.setdefault('stop_errors', []).append(str(stop_error))
        raise
    finally:
        row['completed_at'] = time.time()
        with Path(record_path).open('a') as stream:
            stream.write(json.dumps(row) + '\n')


def measured_eef_action(piper, initial, transform, tcp_offset):
    actual = eef_initial(piper, transform, tcp_offset)
    for i, gripper in ((0, piper.gripper_l), (8, piper.gripper_r)):
        actual[i:i+3] -= initial[i:i+3]
        actual[i+3:i+7] = (R.from_quat(initial[i+3:i+7]).inv() * R.from_quat(actual[i+3:i+7])).as_quat()
        msg = gripper.get_gripper_status()
        if msg is None or not np.isfinite(msg.msg.value):
            raise RuntimeError("Missing measured gripper feedback")
        actual[i+7] = np.clip(msg.msg.value/0.08, 0, 1)
    return actual


def _inference_loop(piper, camera, client, cfg, logger, smoother=None):
    """核心推理循环 — 支持 MOVE_J / MOVE_JS / CPV 模式。"""

    mode = cfg.control_mode
    prompt = cfg.prompt
    action_dt = cfg.action_dt
    wait_tol = np.radians(cfg.wait_tol_deg)
    status = InferenceStatus()

    # ── 重置 Server ──
    status.phase = "reset"
    t0 = time.time()
    print("  → 重置 Server...")
    metadata = client.get_server_metadata() or {}
    if cfg.max_chunks > 1 and not metadata.get('executed_action_history'):
        raise RuntimeError('Continuous execution requires server executed_action_history support; no actions sent')
    if "icl_ids" in metadata:
        if cfg.icl_id not in metadata["icl_ids"]:
            raise ValueError("Unknown demonstration: " + cfg.icl_id)
    if cfg.icl_id:
        client.infer({"reset": True, "prompt": prompt, "use_icl": True,
                      "icl_id": cfg.icl_id})
    else:
        client.reset(prompt)
    print(f"    重置完成 ({time.time()-t0:.2f}s)")

    # ── 等待相机就绪 ──
    print("  → 等待相机画面...")
    time.sleep(3.0)
    cam_high, cam_left, cam_right = camera.get_frames()
    while cam_high is None:
        time.sleep(0.5)
        print("high none")
        cam_high, cam_left, cam_right = camera.get_frames()
    print(f"    相机画面已就绪 (高:{cam_high.shape}, 左:{cam_left.shape}, 右:{cam_right.shape})")

    # ── 获取初始状态 ──
    joint_angles = piper.get_joint_angles()
    print(f"  → 初始关节角 (左臂, deg): {np.degrees(joint_angles[:6]).round(1)}")
    print(f"  → 初始关节角 (右臂, deg): {np.degrees(joint_angles[6:12]).round(1)}")

    initial_obs = format_obs(cam_high, cam_left, cam_right, joint_angles, prompt)

    # Same frame/TCP options and missing-file fallback as the old EEF client.
    eef_transform = None
    if not cfg.no_transform:
        try:
            eef_transform = compute_base_transform()
        except FileNotFoundError as exc:
            print(f"[EEF] {exc}; using each arm's own base, as in the legacy client")
    init_eef = eef_initial(piper, eef_transform, cfg.tcp_offset)
    first = True
    total_steps = 0

    for chunk_idx in range(cfg.max_chunks):
        need_stop = _check_emergency_stop()
        if need_stop:
            break
        print(f"\n  ═══ Chunk {chunk_idx + 1}/{cfg.max_chunks} ═══")

        # ── 发送观测，获取动作 ──
        status.phase = "infer"
        infer_msg = {"obs": initial_obs, "prompt": prompt}
        t_infer_start = time.time()
        ret = client.infer(infer_msg)
        t_infer_s = time.time() - t_infer_start
        logger.log_server_infer(t_infer_s)

        action = ret['action']

        if action.ndim != 3 or action.shape[0] not in (14, 16, 28) or not np.isfinite(action).all():
            raise ValueError(f"Expected finite action [14/16/28,F,A], got {action.shape}")
        if action.shape[0] == 16:
            if action.shape != (16, 2, 16):
                raise ValueError(f"Expected Zero-WAM EEF shape (16,2,16), got {action.shape}")
            if mode == "cpv":
                raise ValueError("16D EEF uses checked IK / MOVE_J; launch with --control_mode movej or movejs")
            executed = np.zeros_like(action)
            key_frames = []
            print("[EEF] 16D relative EEF -> flange -> checked local IK -> MOVE_J")
            np.save(logger.save_dir / f"eef_prediction_{chunk_idx:03d}.npy", action)
            for frame_idx in range(1 if first else 0, action.shape[1]):
                for sample_idx in range(action.shape[2]):
                    if _check_emergency_stop():
                        return total_steps
                    if time.time() - t_infer_start > 60:
                        raise RuntimeError("EEF prediction older than 60 seconds")
                    step_start = time.monotonic()
                    command = relative_eef_to_piper(action[:,frame_idx,sample_idx], init_eef, eef_transform, cfg.tcp_offset)
                    if getattr(cfg, 'eef_step_limit', 0) or getattr(cfg, 'eef_rotation_limit', 0):
                        command = limit_eef_command(command, checked_eef_feedback(piper),
                            cfg.eef_step_limit, cfg.eef_rotation_limit)
                    send_eef_checked(piper, command, logger.save_dir / 'eef_commands.jsonl',
                                     hold_grippers=getattr(cfg, 'hold_grippers', False))
                    total_steps += 1
                    time.sleep(max(0, action_dt-(time.monotonic()-step_start)))
                    if (getattr(cfg, 'hold_grippers', False)
                            and 0 < getattr(cfg, 'max_eef_actions', 0) <= total_steps):
                        # An arm-only terminal probe needs no gripper telemetry/history.
                        np.save(logger.save_dir / 'eef_final_flange.npy', checked_eef_feedback(piper))
                        print(f"[EEF] reached_step={total_steps}; arm-only probe complete", flush=True)
                        return total_steps
                    executed[:,frame_idx,sample_idx] = measured_eef_action(piper, init_eef, eef_transform, cfg.tcp_offset)
                    if (sample_idx+1) % 4 == 0:
                        cams = camera.get_frames()
                        obs = format_obs(*cams, piper.get_joint_angles(), prompt)
                        key_frames.append(obs)
                        cv2.imwrite(str(logger.save_dir/f"eef_observation_{total_steps:04d}.jpg"), np.hstack(cams))
                    print(f"[EEF] reached_step={total_steps} L={command[0][:3]} R={command[1][:3]}",flush=True)
                    if 0 < getattr(cfg, 'max_eef_actions', 0) <= total_steps:
                        # Save only real samples; a partial chunk must not enter KV history.
                        completed = executed[:, 1 if first else 0:frame_idx+1, :]
                        completed = completed.reshape(16, -1)[:, :
                            (frame_idx-(1 if first else 0))*action.shape[2]+sample_idx+1]
                        np.save(logger.save_dir / f"eef_measured_partial_{chunk_idx:03d}.npy", completed)
                        return total_steps
            np.save(logger.save_dir / f"eef_measured_{chunk_idx:03d}.npy", executed)
            if chunk_idx + 1 < cfg.max_chunks:
                if not metadata.get("executed_action_history"):
                    raise RuntimeError("Server cannot accept measured EEF history")
                client.infer({"obs": key_frames, "compute_kv_cache": True, "executed_actions": executed})
            first = False
            initial_obs = format_obs(*camera.get_frames(), piper.get_joint_angles(), prompt)
            continue
        assert action.shape[2] % 4 == 0, \
            f"action_per_frame must be divisible by 4, got {action.shape[2]}"
        action_per_frame = action.shape[2] // 4

        total_action_steps = action.shape[1] * action.shape[2]

        # ── 收集原始动作 ──
        all_raw = np.empty((total_action_steps, 14), dtype=np.float64)
        for i in range(action.shape[1]):
            for j in range(action.shape[2]):
                step = action[:, i, j].flatten()
                if step.shape[0] == 28:
                    step = np.concatenate([step[14:20], step[26:27], step[20:26], step[27:28]], axis=0)
                all_raw[i * action.shape[2] + j] = step

        # ── 平滑处理 ──
        if smoother is not None:
            all_exec, orig_indices = smoother.smooth_full_action(all_raw)
            print(f"收到 action{action.shape} "
                  f"({t_infer_s:.2f}s, 原始 {total_action_steps} 步 → 平滑后 {len(all_exec)} 步)")
        else:
            all_exec = all_raw
            orig_indices = np.arange(len(all_raw))
            print(f"收到 action{action.shape} "
                  f"({t_infer_s:.2f}s, 将执行 {total_action_steps} 步)")

        obs_indices = set(int(orig_indices[i]) for i in range(len(orig_indices))
                          if (i + 1) % action_per_frame == 0)

        # ── 执行动作帧 ──
        status.phase = "exec"
        t_exec_start = time.time()
        key_frame_list = []

        exec_start_idx = int(orig_indices[action.shape[2]]) if first else 0

        for step_idx in range(exec_start_idx, len(all_exec)):
            step_start = time.monotonic()
            action_step = all_exec[step_idx]

            left_joints, right_joints, left_g, right_g = \
                model_joint_action_to_piper(action_step)
            left_g, right_g = left_g * 0.80, right_g * 0.90
            if left_g <= 0.02:
                left_g = 0
            if right_g <= 0.02:
                right_g = 0

            send_action(piper, left_joints, right_joints, left_g, right_g, mode, first)
            total_steps += 1

            # ── 关键帧处理 ──
            is_key = (step_idx in obs_indices)

            if is_key:
                piper.wait_both_motion_done(
                    left_joints=left_joints, right_joints=right_joints,
                    timeout=3, poll_interval=0.01, tol_rad=wait_tol)
                cam_h, cam_l, cam_r = camera.get_frames()
                ja = piper.get_joint_angles()
                obs = format_obs(cam_h, cam_l, cam_r, ja, prompt)
                key_frame_list.append(obs)

                if cam_h is not None:
                    key_frame_dir = Path(cfg.save_dir) / logger.save_dir.name / "key_frames"
                    key_frame_dir.mkdir(parents=True, exist_ok=True)
                    combined = np.hstack([cam_h, cam_l, cam_r])
                    cv2.imwrite(str(key_frame_dir / f"step_{total_steps:04d}.jpg"), combined)

                logger.log_key_frame(chunk_idx, total_steps, cam_h, cam_l, cam_r, action_step)
            else:
                elapsed = time.monotonic() - step_start
                sleep_time = action_dt - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            # 状态 & 打印
            status.step_idx = total_steps
            status.chunk_idx = chunk_idx + 1
            status.elapsed_total = time.time() - logger.start_time
            status.elapsed_infer = t_infer_s
            status.elapsed_exec = time.time() - t_exec_start
            status.left_gripper = left_g
            status.right_gripper = right_g

            lj = np.degrees(left_joints)
            rj = np.degrees(right_joints)
            actual_dt = time.monotonic() - step_start
            print(f"    [{total_steps:3d}] t={status.elapsed_total:6.1f}s dt={actual_dt*1000:5.1f}ms | "
                  f"L=({lj[0]:6.1f},{lj[1]:6.1f},{lj[2]:6.1f},"
                  f"{lj[3]:6.1f},{lj[4]:6.1f},{lj[5]:6.1f})deg "
                  f"R=({rj[0]:6.1f},{rj[1]:6.1f},{rj[2]:6.1f},"
                  f"{rj[3]:6.1f},{rj[4]:6.1f},{rj[5]:6.1f})deg "
                  f"gL={left_g:.3f} gR={right_g:.3f}", flush=True)

        t_exec_s = time.time() - t_exec_start
        first = False

        # ── 更新 KV 缓存 ──
        t_kv_s = 0.0
        if key_frame_list:
            status.phase = "kv_cache"
            print(f"    [KV缓存] 更新 {len(key_frame_list)} 帧...", end=" ", flush=True)
            t_kv = time.time()
            client.compute_kv_cache(key_frame_list, action)
            t_kv_s = time.time() - t_kv
            logger.log_kv_cache(t_kv_s)
            print(f"完成 ({t_kv_s:.2f}s)")

        # 为下一个 chunk 准备初始观测
        piper.wait_both_motion_done(
            left_joints=left_joints, right_joints=right_joints,
            timeout=3, poll_interval=0.01, tol_rad=wait_tol)
        cam_high, cam_left, cam_right = camera.get_frames()
        if cam_high is not None:
            joint_angles = piper.get_joint_angles()
            initial_obs = format_obs(cam_high, cam_left, cam_right, joint_angles, prompt)

        print(f"    Chunk {chunk_idx + 1} 完成: "
              f"推理 {t_infer_s:.2f}s + 执行 {t_exec_s:.2f}s + KV {t_kv_s:.2f}s = "
              f"{t_infer_s + t_exec_s + t_kv_s:.2f}s")

    print(f"\n  推理完成，共执行 {total_steps} 步")
    return total_steps


# ─────────────────────── 入口 ───────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="Piper 双臂 — LingBot VA 实时推理 (MOVE_J / CPV 双模式)"
    )
    parser.add_argument("--prompt", type=str, required=True,
                        help="任务描述")
    parser.add_argument("--control_mode", type=str, default="movej",
                        choices=["origin", "movej", "movejs", "cpv"],
                        help="控制模式 (默认: movej)")
    parser.add_argument("--max_chunks", type=int, default=50,
                        help="最大推理 chunk 数 (默认: 50)")
    parser.add_argument("--icl-id", default="bread_plate_20261008_v1")
    parser.add_argument("--server-host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=39056)
    parser.add_argument("--no_transform", action="store_true")
    parser.add_argument("--tcp_offset", type=float, default=0.0)
    parser.add_argument("--firmware-version", default="v188",
                        choices=["default", "v183", "v188"],
                        help="SDK profile; this site's S-V1.8-8 arms use v188")
    parser.add_argument("--max-eef-actions", type=int, default=0,
                        help="Stop after this many confirmed EEF arrivals; 0 completes chunks")
    parser.add_argument("--eef-step-limit", type=float, default=0.0,
                        help="Explicit per-action flange translation limit in meters; 0 disables")
    parser.add_argument("--eef-rotation-limit", type=float, default=0.0,
                        help="Explicit per-action flange rotation limit in radians; 0 disables")
    parser.add_argument("--hold-grippers", action="store_true")
    cli = parser.parse_args()
    if cli.max_eef_actions < 0:
        parser.error("--max-eef-actions must be non-negative")
    if any(not np.isfinite(x) or x < 0 for x in (cli.eef_step_limit, cli.eef_rotation_limit)):
        parser.error("EEF limits must be finite and non-negative")

    cls = CONFIG_MAP[cli.control_mode]
    if cli.control_mode in ("movej", "movejs"):
        cfg = cls(control_mode=cli.control_mode)
    else:
        cfg = cls()
    cfg.prompt = cli.prompt
    cfg.max_chunks = cli.max_chunks
    cfg.icl_id = cli.icl_id
    cfg.server_host, cfg.server_port = cli.server_host, cli.server_port
    cfg.no_transform, cfg.tcp_offset = cli.no_transform, cli.tcp_offset
    cfg.firmware_version = cli.firmware_version
    cfg.max_eef_actions = cli.max_eef_actions
    cfg.eef_step_limit, cfg.eef_rotation_limit = cli.eef_step_limit, cli.eef_rotation_limit
    cfg.hold_grippers = cli.hold_grippers
    return cfg


if __name__ == "__main__":
    cfg = parse_args()
    run_inference(cfg)
