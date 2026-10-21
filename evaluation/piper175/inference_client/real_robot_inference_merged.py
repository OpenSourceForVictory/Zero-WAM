"""
真实 Piper 双臂机械臂推理脚本 — 支持 MOVE_J / CPV 双模式
运行环境: piper_new

用法:
    python real_robot_inference_merged.py --prompt "Pick up the cup" --control_mode movej
    python real_robot_inference_merged.py --prompt "Pick up the cup" --control_mode cpv
"""

import argparse
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from stop_request import StopRequested
from action_contract import validate_reset, execution_tensor, gripper_targets
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from site_controller import PiperController
from camera_manager import CameraManager
from va_client import WebsocketClientPolicy
from realtime_visualizer import InferenceStatus
from joint_trajectory_smoother import JointTrajectorySmoother

os.environ["KINETO_LOG_LEVEL"] = "5"


# ─────────────────────── 配置 ───────────────────────


@dataclass
class BaseConfig:
    """共享参数"""
    prompt: str = ""
    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 39056
    # CAN
    can_left: str = "can_left"
    can_right: str = "can_right"
    # Camera
    cam_high: str = "/camera_f/color/image_raw"
    cam_left: str = "/camera_l/color/image_raw"
    cam_right: str = "/camera_r/color/image_raw"
    cam_width: int = 640
    cam_height: int = 480
    cam_fps: int = 30
    # Execution
    max_chunks: int = 50
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

    def diagnostic(self, name, value):
        try:
            with (self.save_dir / (name + '.jsonl')).open('a') as f:
                f.write(json.dumps(value, allow_nan=False) + '\n')
        except (OSError, ValueError) as exc:
            print('Diagnostic write failed:', exc)

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
    """Send half-resolution RGB observations; retain original capture/recording frames."""
    cam_high = cv2.cvtColor(cv2.resize(cam_high, (320, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    cam_left = cv2.cvtColor(cv2.resize(cam_left, (320, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    cam_right = cv2.cvtColor(cv2.resize(cam_right, (320, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
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
    """175 entry: check by default, explicit shadow or execute."""
    camera = CameraManager()
    piper = PiperController()
    piper.stop_check = _check_emergency_stop
    piper.max_target_delta_deg = cfg.max_target_delta_deg
    piper.gripper_mode = cfg.gripper_mode
    client = WebsocketClientPolicy(host=cfg.server_host, port=cfg.server_port)
    save_dir = Path(cfg.save_dir) / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + cfg.run_mode)
    logger = InferenceLogger(str(save_dir), cfg.prompt)
    recorder = None
    recorder_log = None
    error = None
    total_steps = 0
    stopped = False
    monitor = None
    software_stop_latched = False
    latch_error = None
    def latch_stop():
        nonlocal software_stop_latched, latch_error
        from fault_stop import latch_software_stop
        try:
            latch_software_stop(Path(__file__).with_name('runtime_config.json'))
            software_stop_latched = True
        except Exception as exc:
            latch_error = repr(exc)
            print(f'Failed to persist software stop: {exc}; this process still exits', flush=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Signal {signum}")
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if _check_emergency_stop():
            raise StopRequested('Runtime stop is set; clear it explicitly before starting')
        camera.start()
        piper.connect()  # Feedback only; never enables.
        if getattr(cfg, 'monitor_file', None):
            from inference_monitor import InferenceMonitor, hardware_probe
            monitor = InferenceMonitor(cfg.monitor_file, Path(__file__).with_name('runtime_config.json'), lambda: hardware_probe(piper))
            monitor.update(output=str(save_dir), mode=cfg.run_mode, prompt=cfg.prompt)
            monitor.start()
            logger.monitor = monitor
        state = piper.get_joint_angles()
        frames = camera.get_frames()
        metadata = None if cfg.skip_server else client.connect()
        logger.start('movej')
        client.diagnostics_path = logger.save_dir / 'rpc_diagnostics.jsonl'
        manifest = {'mode': cfg.run_mode, 'prompt': cfg.prompt,
            'can_left': piper.can_left, 'can_right': piper.can_right,
            'camera_serials_high_left_right': ['CC1C8100041','CC1C810002Y','CC1C8100015'],
            'camera_shapes': [list(f.shape) for f in frames],
            'request_image_shapes': [[240, 320, 3]] * 3,
            'camera_timestamps': camera.timestamps.tolist(),
            'joint_rad': state.tolist(), 'server_metadata': metadata,
            'server_host': cfg.server_host, 'server_port': cfg.server_port,
            'gripper_firmware_max_m': piper.gripper_max,
            'gripper_mode': cfg.gripper_mode,
            'icl_latent_path': os.environ.get('ZERO_WAM_ICL_LATENT_PATH'),
            'icl_id': os.environ.get('ZERO_WAM_ICL_ID'),
            'speed_percent': int(cfg.movej_speed_percent*100),
            'max_target_delta_deg': cfg.max_target_delta_deg}
        (save_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=str))
        np.savez(save_dir / 'initial_observation.npz', high=frames[0], left=frames[1], right=frames[2], state=state, timestamps=camera.timestamps)
        print(json.dumps(manifest, indent=2, default=str))
        if cfg.run_mode == 'check':
            print('CHECK PASSED: no reset, enable, mode change or movement sent')
            return
        if getattr(cfg, 'record_continuous', False):
            recorder_log = (save_dir / 'recorder.log').open('w')
            recorder = subprocess.Popen([sys.executable, str(Path(__file__).with_name('continuous_recorder.py')),
                '--output', str(save_dir / 'continuous'), '--stop-file', str(save_dir / 'recorder.stop')],
                stdout=recorder_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 20
            while not (save_dir / 'continuous' / 'ready.json').exists():
                if recorder.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Continuous camera recorder failed to start; see recorder.log')
                time.sleep(.1)
            logger.recorder = recorder
        smoother = JointTrajectorySmoother(smooth_method=cfg.smooth_method,
            smooth_factor=cfg.smooth_factor, v_max=cfg.v_max, dt=cfg.action_dt,
            outlier_threshold_deg=cfg.outlier_threshold, savgol_window=cfg.savgol_window,
            savgol_order=cfg.savgol_order, butter_cutoff=cfg.butter_cutoff)
        total_steps = _inference_loop(piper, camera, client, cfg, logger, smoother)
    except (StopRequested, KeyboardInterrupt) as exc:
        stopped = True
        latch_stop()
        print(f"User stop: {exc}; disconnect only, no electronic stop/reset/disable", flush=True)
    except BaseException as exc:
        error = repr(exc)
        latch_stop()
        # Electronic stop on this Piper pair can cause the arms to descend.
        # Software failures abort this loop and latch its gate; never convert
        # them into controller stop/reset/disable/hold/recovery commands.
        print(f'Execution aborted: {exc}; stopping command transmission and disconnecting. '
              'No automatic electronic stop/reset/disable or recovery. '
              'An already accepted target may still execute; position hold is not guaranteed.', flush=True)
        raise
    finally:
        if monitor is not None:
            monitor.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if recorder is not None:
            (save_dir / 'recorder.stop').touch()
            try:
                recorder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                recorder.terminate()
                recorder.wait(timeout=5)
            recorder_log.close()
        try:
            if logger.video_writers:
                for writer in logger.video_writers.values():
                    writer.release()
        except Exception as exc:
            print(f'Video finalization failed: {exc}', flush=True)
        try:
            if save_dir.exists():
                (save_dir / 'result.json').write_text(json.dumps({
                    'mode': cfg.run_mode, 'completed': error is None and not stopped, 'stopped_by_user': stopped, 'error': error,
                    'completed_loop_steps': total_steps,
                    'sent_steps': getattr(logger, 'sent_steps', 0),
                    'software_stop_latched': software_stop_latched, 'stop_latch_error': latch_error,
                    'automatic_electronic_stop_sent': False,
                    'fault_response': 'stop_sending_and_disconnect' if error else None,
                    'actuation_started': piper.actuated, 'task_success': None}, indent=2))
        except Exception as exc:
            print(f'Result log failed: {exc}', flush=True)
        try:
            client.close()
        finally:
            try:
                camera.stop()
            finally:
                piper.close()


def _check_emergency_stop():
    _RUNTIME_CONFIG_PATH = Path(__file__).with_name("runtime_config.json")
    """读取 runtime_config.json，如果 emergency_stop=True 则抛出异常。"""
    try:
        with open(_RUNTIME_CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        if cfg.get("emergency_stop", False):
            return True
        return False
    except (OSError, json.JSONDecodeError):
        return True


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
    reset_reply = client.reset(prompt)
    validate_reset(reset_reply, cfg.action_contract)
    logger.diagnostic("action_contract", {"mode": cfg.action_contract, "reset": reset_reply})
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

    first = True
    total_steps = 0

    for chunk_idx in (itertools.count() if cfg.max_chunks == 0 else range(cfg.max_chunks)):
        if getattr(logger, 'recorder', None) is not None and logger.recorder.poll() is not None:
            raise RuntimeError('Continuous recorder exited; see recorder.log')
        need_stop = _check_emergency_stop()
        if need_stop:
            raise StopRequested('Runtime stop requested')
        print(f"\n  ═══ Chunk {chunk_idx + 1}/{cfg.max_chunks if cfg.max_chunks else '不限'} ═══")

        # ── 发送观测，获取动作 ──
        status.phase = "infer"
        if getattr(logger,"monitor",None):logger.monitor.update(phase="predict",chunk=chunk_idx+1)
        infer_msg = {"obs": initial_obs, "prompt": prompt}
        t_infer_start = time.time()
        ret = client.infer(infer_msg)
        t_infer_s = time.time() - t_infer_start
        logger.log_server_infer(t_infer_s)

        if 'prediction_artifacts' in ret:
            logger.diagnostic('prediction_artifacts', ret['prediction_artifacts'])
        action = np.asarray(ret['action'])
        if action.ndim != 3 or not np.isfinite(action).all() or action.shape[0] not in (14, 28) or min(action.shape[1:]) <= 0:
            raise ValueError(f'Invalid joint action tensor: {action.shape}')
        np.save(logger.save_dir / f'prediction_{chunk_idx:04d}.npy', action)
        executable_action = execution_tensor(action, cfg.action_contract)
        from action_diagnostics import summarize_action
        logger.diagnostic('action_diagnostics', {'chunk': chunk_idx + 1,
            'summary': summarize_action(executable_action, first_chunk=first, current_joints=initial_obs['observation.state'][:12]),
            'task_success': None})
        if cfg.run_mode == 'shadow':
            print(f'SHADOW prediction saved: {action.shape}; no enable or movement sent')
            return 0
        # Reject changed hardware/camera/stop state after a slow inference request.
        piper.check_ready()
        camera.get_frames()

        assert action.shape[0] == 14 or action.shape[0] == 28, \
            f"Expected action dim 14, got {action.shape[0]}"
        assert action.shape[2] % 4 == 0, \
            f"action_per_frame must be divisible by 4, got {action.shape[2]}"
        action_per_frame = action.shape[2] // 4

        total_action_steps = action.shape[1] * action.shape[2]

        # ── 收集原始动作 ──
        all_raw = np.empty((total_action_steps, 14), dtype=np.float64)
        for i in range(action.shape[1]):
            for j in range(action.shape[2]):
                step = executable_action[:, i, j].flatten()
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
        if getattr(logger,"monitor",None):logger.monitor.update(phase="execute",infer_s=t_infer_s)
        t_exec_start = time.time()
        key_frame_list = []
        
        if first and (len(orig_indices) <= action.shape[2]):
            raise ValueError('Too few predicted steps after initial latent skip')
        exec_start_idx = int(orig_indices[action.shape[2]]) if first else 0
        if exec_start_idx >= len(all_exec):
            raise ValueError('Initial latent skip leaves no executable steps')

        for step_idx in range(exec_start_idx, len(all_exec)):
            step_start = time.monotonic()
            action_step = all_exec[step_idx]

            left_joints, right_joints, left_g, right_g = \
                model_joint_action_to_piper(action_step)
            left_g, right_g = gripper_targets(left_g, right_g, cfg.action_contract)

            if _check_emergency_stop():
                raise StopRequested('Runtime stop requested')
            piper.validate_target(left_joints, right_joints)
            if not piper.actuated:
                piper.enable()
                piper.robot_l.set_speed_percent(int(cfg.movej_speed_percent * 100))
                piper.robot_r.set_speed_percent(int(cfg.movej_speed_percent * 100))
                restore_movej_mode(piper.robot_l)
                restore_movej_mode(piper.robot_r)
            send_action(piper, left_joints, right_joints, left_g, right_g, mode, first)
            total_steps += 1
            logger.sent_steps = total_steps
            if getattr(logger,"monitor",None):logger.monitor.update(sent_steps=total_steps)

            # ── 关键帧处理 ──
            is_key = (step_idx in obs_indices)

            if is_key:
                wait_ok = piper.wait_both_motion_done(
                    left_joints=left_joints, right_joints=right_joints,
                    timeout=3, poll_interval=0.01, tol_rad=wait_tol)
                cam_h, cam_l, cam_r = camera.get_frames()
                ja = piper.get_joint_angles()
                target = np.asarray(left_joints + right_joints)
                error_deg = np.rad2deg(np.asarray(ja) - target)
                logger.diagnostic('execution_feedback', {'chunk': chunk_idx + 1, 'sent_step': total_steps,
                    'target_deg': np.rad2deg(target).tolist(), 'feedback_deg': np.rad2deg(ja).tolist(),
                    'error_deg': error_deg.tolist(), 'max_abs_error_deg': float(np.abs(error_deg).max()),
                    'wait_returned': bool(wait_ok), 'camera_timestamps': camera.timestamps.tolist(),
                    'sample_time': time.time(), 'task_success': None})
                if getattr(logger,"monitor",None):
                    logger.monitor.update(tracking_error_deg=float(np.abs(error_deg).max()),wait_ok=bool(wait_ok))
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
        logger.timings['action_exec_s'].append(t_exec_s)
        first = False

        # ── 更新 KV 缓存 ──
        t_kv_s = 0.0
        if key_frame_list:
            status.phase = "kv_cache"
            if getattr(logger,"monitor",None):logger.monitor.update(phase="kv_update",execute_s=t_exec_s)
            print(f"    [KV缓存] 更新 {len(key_frame_list)} 帧...", end=" ", flush=True)
            t_kv = time.time()
            client.compute_kv_cache(key_frame_list, action)
            t_kv_s = time.time() - t_kv
            logger.log_kv_cache(t_kv_s)
            if getattr(logger,"monitor",None):logger.monitor.update(kv_s=t_kv_s)
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
        logger.diagnostic('chunk_timings', {'chunk': chunk_idx + 1, 'infer_s': t_infer_s,
            'execute_s': t_exec_s, 'kv_s': t_kv_s, 'sent_steps_cumulative': total_steps})

    print(f"\n  推理完成，共执行 {total_steps} 步")
    return total_steps


# ─────────────────────── 入口 ───────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description='174 merged client adapted to Piper 175')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_const', dest='run_mode', const='check')
    modes.add_argument('--shadow', action='store_const', dest='run_mode', const='shadow')
    modes.add_argument('--execute', action='store_const', dest='run_mode', const='execute')
    parser.set_defaults(run_mode='check')
    parser.add_argument('--monitor-file', default=None, help='Opt-in read-only hardware watchdog and UI heartbeat path')
    parser.add_argument('--record-continuous', action='store_true')
    parser.add_argument('--action-contract', choices=['legacy174', 'piperpika'], default='legacy174')
    parser.add_argument('--prompt', default='breakfast')
    parser.add_argument('--control_mode', choices=['movej'], default='movej')
    parser.add_argument('--max_chunks', type=int, default=0, help='0 for unlimited execution (174 launcher default)')
    parser.add_argument('--server_host', default='127.0.0.1')
    parser.add_argument('--server_port', type=int, default=39056)
    parser.add_argument('--skip-server', action='store_true', help='Hardware check only')
    parser.add_argument('--speed-percent', type=int, default=6, choices=range(1,7))
    parser.add_argument('--max-target-delta-deg', type=float, default=None, help='Optional target delta guard in degrees; disabled by default to match 174')
    parser.add_argument('--gripper-mode', choices=['hold','legacy174'], default='legacy174',
                        help='hold sends no gripper commands; legacy174 sends gripper commands; action-contract selects scaling; firmware caps remain')
    parser.add_argument('--save_dir', default=str(Path(__file__).with_name('inference_logs')))
    cli = parser.parse_args()
    if cli.max_chunks < 0 or (cli.max_target_delta_deg is not None and (not np.isfinite(cli.max_target_delta_deg) or not 0 < cli.max_target_delta_deg <= 30)):
        parser.error('Invalid chunk count or maximum target delta (0 < delta <= 30 deg)')
    if cli.skip_server and cli.run_mode != 'check':
        parser.error('--skip-server is only valid with --check')
    cfg = MoveJConfig()
    for key, value in vars(cli).items():
        setattr(cfg, key, value)
    cfg.movej_speed_percent = cli.speed_percent / 100
    return cfg


if __name__ == "__main__":
    cfg = parse_args()
    run_inference(cfg)
