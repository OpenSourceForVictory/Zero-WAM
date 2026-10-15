"""
实时推理状态显示器 (终端版)
在终端中实时显示推理进度
"""

import time
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class InferenceStatus:
    """推理状态"""
    phase: str = "idle"  # idle, reset, infer, exec, kv_cache
    chunk_idx: int = 0
    chunk_total: int = 0
    step_idx: int = 0
    max_chunks: int = 0
    elapsed_total: float = 0.0
    elapsed_infer: float = 0.0
    elapsed_exec: float = 0.0
    elapsed_kv: float = 0.0

    # 左臂状态
    left_pos: tuple = (0.0, 0.0, 0.0)  # mm
    left_gripper: float = 0.0

    # 右臂状态
    right_pos: tuple = (0.0, 0.0, 0.0)  # mm
    right_gripper: float = 0.0


class RealtimeVisualizer:
    """实时推理状态显示器 (终端输出)"""

    PHASE_COLORS = {
        "idle": "\033[90m",      # 灰色
        "reset": "\033[93m",     # 黄色
        "infer": "\033[92m",     # 绿色
        "exec": "\033[94m",      # 蓝色
        "kv_cache": "\033[95m",  # 紫色
    }
    PHASE_NAMES = {
        "idle": "等待中",
        "reset": "重置Server",
        "infer": "模型推理中",
        "exec": "执行动作",
        "kv_cache": "更新KV缓存",
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def __init__(self, prompt: str, max_chunks: int):
        self.prompt = prompt
        self.max_chunks = max_chunks
        self.status = InferenceStatus()
        self._last_print_len = 0

    def _clear_line(self):
        """清除当前行"""
        print("\r" + " " * self._last_print_len + "\r", end="", flush=True)

    def _print_status_bar(self, width: int = 60) -> str:
        """生成进度条"""
        if self.max_chunks <= 0:
            return "[" + "?" * (width - 2) + "]"

        filled = int((self.status.chunk_idx / self.max_chunks) * (width - 2))
        filled = min(filled, width - 2)
        bar = "[" + "=" * filled + "-" * (width - 2 - filled) + "]"
        percent = (self.status.chunk_idx / self.max_chunks) * 100
        return f"{bar} {percent:5.1f}%"

    def update(self, cam_high, cam_left, cam_right, status: InferenceStatus):
        """更新状态显示"""
        self.status = status
        status.max_chunks = self.max_chunks

        # 清除上一行
        self._clear_line()

        # 构建状态行
        phase_color = self.PHASE_COLORS.get(status.phase, "")
        phase_name = self.PHASE_NAMES.get(status.phase, status.phase)

        # 时间信息
        time_str = f"t={status.elapsed_total:6.1f}s"
        infer_str = f"infer={status.elapsed_infer:.2f}s" if status.elapsed_infer > 0 else ""
        exec_str = f"exec={status.elapsed_exec:.2f}s" if status.elapsed_exec > 0 else ""

        # 位置信息
        lx, ly, lz = status.left_pos
        rx, ry, rz = status.right_pos
        pos_str = f"L=({lx:6.1f},{ly:6.1f},{lz:6.1f}) R=({rx:6.1f},{ry:6.1f},{rz:6.1f})mm"

        # 组合输出
        header = f"{self.BOLD}[Chunk {status.chunk_idx}/{self.max_chunks} | Step {status.step_idx}]{self.RESET}"
        phase = f"{phase_color}[{phase_name}]{self.RESET}"
        progress = self._print_status_bar(40)

        line = f"{header} {time_str} {phase} {pos_str}"

        self._last_print_len = len(line) + 10
        print(f"\r{line}", end="", flush=True)

    def print_summary(self):
        """打印摘要（换行）"""
        print()  # 换行

    def close(self):
        """关闭显示"""
        self._clear_line()
        print(f"\r{self.BOLD}[推理结束]{self.RESET} 总步数={self.status.step_idx}, 总时间={self.status.elapsed_total:.1f}s")
