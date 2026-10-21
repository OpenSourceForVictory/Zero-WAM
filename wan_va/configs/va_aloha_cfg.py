"""Released RoboCOIN agilex_cobot_decoupled_magic EEF inference profile.

Dataset parameters, not a calibration of the user's physical robot.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import os

from .va_robotwin_cfg import va_robotwin_cfg, load_robotwin_norm_stat

va_aloha_cfg = deepcopy(va_robotwin_cfg)
va_aloha_cfg.__name__ = 'Config: RoboCOIN Split ALOHA physical EEF'
va_aloha_cfg.model_path = os.environ.get('MODEL_PATH', '/path/to/zero-wam-pretrain')
_stats = Path(__file__).resolve().parents[1] / 'assets/norm_stats/robocoin_split_aloha.json'
va_aloha_cfg.action_stats_path = str(_stats)
va_aloha_cfg.action_stats_sha256 = hashlib.sha256(_stats.read_bytes()).hexdigest()
va_aloha_cfg.norm_stat = load_robotwin_norm_stat(_stats)
va_aloha_cfg.action_per_frame = 8
va_aloha_cfg.model_action_dt_s = 1 / 30
va_aloha_cfg.model_video_fps = 15
va_aloha_cfg.action_convention = 'episode_relative_physical_xyzw'
va_aloha_cfg.gripper_convention = 'absolute gripper_open_scale_state'
va_aloha_cfg.dataset_robot_type = 'agilex_cobot_decoupled_magic'
va_aloha_cfg.deployment_validated = False
# Offline endpoint by default; existing hardware clients must not auto-connect.
va_aloha_cfg.host = '127.0.0.1'
va_aloha_cfg.port = 29058
