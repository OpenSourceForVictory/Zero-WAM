"""
Joint 轨迹后处理平滑器.

对模型输出的绝对关节角序列做后处理, 消除步间高频抖动,
可选速度约束重采样, 生成可直接发给 Piper 的平滑轨迹.

方法:
  - spline:   Smoothing spline (scipy.interpolate.splrep), 可调平滑因子
  - savgol:   Savitzky-Golay 滑动窗口多项式滤波
  - butter:   Butterworth 低通滤波 (零相位)
  - none:     不做平滑 (仅异常值过滤 + 速度约束)
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import splrep, splev

# ──────────────────────── 关节索引常量 ────────────────────────

# 14 维 action 中各关节的索引
JOINT_INDICES_14 = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]  # 12 个关节 (排除了夹爪)
GRIPPER_INDICES_14 = [6, 13]  # 2 个夹爪
N_JOINTS = 12


class JointTrajectorySmoother:
    """对模型输出的关节角序列做后处理平滑."""

    def __init__(
        self,
        smooth_method: str = "spline",
        smooth_factor: float = 0.1,
        v_max: float = 3.0,
        dt: float = 0.1,
        outlier_threshold_deg: float = 10.0,
        savgol_window: int = 7,
        savgol_order: int = 3,
        butter_cutoff: float = 5.0,
    ):
        """
        Parameters
        ----------
        smooth_method : str
            "spline", "savgol", "butter", "none"
        smooth_factor : float
            spline 平滑因子 (s 参数), 越大越平滑, 0=完全插值. 仅 spline 模式.
        v_max : float
            关节最大角速度 (rad/s), 用于速度约束重采样, 0=禁用.
        dt : float
            执行间隔 (秒), 用于速度约束.
        outlier_threshold_deg : float
            异常值检测阈值 (度), 0=禁用.
        savgol_window : int
            Savitzky-Golay 窗口大小 (必须为奇数), 仅 savgol 模式.
        savgol_order : int
            Savitzky-Golay 多项式阶数, 仅 savgol 模式.
        butter_cutoff : float
            Butterworth 截止频率 (Hz), 仅 butter 模式.
        """
        self.smooth_method = smooth_method
        self.smooth_factor = smooth_factor
        self.v_max = v_max
        self.dt = dt
        self.outlier_threshold_deg = outlier_threshold_deg
        self.savgol_window = savgol_window
        self.savgol_order = savgol_order
        self.butter_cutoff = butter_cutoff

    # ────────────────────────── 公共入口 ──────────────────────────

    def smooth(self, actions_2d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """主入口: 对 [N, 12] joint 序列做平滑.

        Args:
            actions_2d: [N, 12] 关节角 (弧度), 12 维 = 左6关节 + 右6关节

        Returns:
            (smoothed, orig_indices): 平滑后的关节角序列, 原始点在输出中的索引
        """
        if actions_2d.ndim != 2 or actions_2d.shape[1] != N_JOINTS:
            raise ValueError(
                f"Expected shape [N, {N_JOINTS}], got {actions_2d.shape}"
            )

        N = len(actions_2d)
        if N < 3:
            return actions_2d.copy(), np.arange(N)

        result = actions_2d.copy()
        orig_indices = np.arange(N)

        # Stage 1: 异常值过滤
        # if self.outlier_threshold_deg > 0:
        #     result = self._filter_outliers(result)

        # Stage 2: 平滑
        splines = None
        if self.smooth_method == "spline":
            result, splines = self._smooth_spline(result, return_splines=True)
        elif self.smooth_method == "savgol":
            result = self._smooth_savgol(result)
        elif self.smooth_method == "butter":
            result = self._smooth_butter(result)
            
        # Stage 3: 速度约束重采样 (时间缩放)
        if self.v_max > 0 and self.dt > 0:
            result, orig_indices = self._resample_velocity_limit(result, splines=splines)

        return result, orig_indices

    def smooth_grippers(self, grippers_2d: np.ndarray) -> np.ndarray:
        """对 [N, 2] 夹爪序列做轻量平滑.

        夹爪对平滑更敏感 (不能过度), 使用小窗口 savgol 或跳过高强度 spline.
        """
        if grippers_2d.ndim != 2 or grippers_2d.shape[1] != 2:
            raise ValueError(f"Expected shape [N, 2], got {grippers_2d.shape}")

        N = len(grippers_2d)
        if N < 3:
            return grippers_2d.copy()

        if self.smooth_method == "none":
            return grippers_2d.copy()

        from scipy.signal import savgol_filter

        result = grippers_2d.copy()
        window = min(5, N - 1 if (N - 1) % 2 == 1 else N - 2)
        if window < 3:
            return result
        for j in range(2):
            result[:, j] = savgol_filter(result[:, j], window, 2)
        return result

    # ──────────────────────── Stage 1: 异常值过滤 ────────────────────────

    def _filter_outliers(self, actions: np.ndarray) -> np.ndarray:
        """检测并替换单帧异常跳变点."""
        N, D = actions.shape
        threshold_rad = np.radians(self.outlier_threshold_deg)
        result = actions.copy()

        for j in range(D):
            for i in range(1, N - 1):
                neighbors = [actions[i - 1, j], actions[i + 1, j]]
                median_val = np.median(neighbors)
                if abs(actions[i, j] - median_val) > threshold_rad:
                    result[i, j] = median_val

        return result

    # ──────────────────────── Stage 2: 平滑算法 ────────────────────────

    def _smooth_spline(self, actions: np.ndarray, return_splines: bool = False):
        """Smoothing spline 拟合 (推荐).

        使用 scipy.interpolate.splrep, s 参数控制平滑强度:
          s=0   → 完全插值 (无平滑)
          s>0   → 允许偏离原始点, 越大越平滑
        默认 s = len(actions) * smooth_factor

        Args:
            actions: [N, D] 输入序列
            return_splines: 若为 True, 额外返回每个关节的 (tck) 元组列表,
                            用于后续 spline-aware 重采样

        Returns:
            result: [N, D] 拟合结果
            splines: 仅当 return_splines=True 时返回, list[tck] 或 None
        """
        N, D = actions.shape
        t = np.arange(N, dtype=np.float64)
        s = self.smooth_factor * N
        result = np.empty_like(actions)
        splines = [] if return_splines else None

        for j in range(D):
            try:
                spl = splrep(t, actions[:, j], s=s)
                result[:, j] = splev(t, spl)
                if return_splines:
                    splines.append(spl)
            except Exception:
                # splrep 失败 (如过少的唯一值), 保留原始值
                print("splrep 失败")
                result[:, j] = actions[:, j]
                if return_splines:
                    splines.append(None)

        if return_splines:
            return result, splines
        return result

    def _smooth_savgol(self, actions: np.ndarray) -> np.ndarray:
        """Savitzky-Golay 滤波."""
        from scipy.signal import savgol_filter

        N, D = actions.shape
        window = min(self.savgol_window, N - 1 if (N - 1) % 2 == 1 else N - 2)
        if window < 3:
            return actions.copy()
        order = min(self.savgol_order, window - 1)
        result = np.empty_like(actions)
        for j in range(D):
            result[:, j] = savgol_filter(actions[:, j], window, order)
        return result

    def _smooth_butter(self, actions: np.ndarray) -> np.ndarray:
        """Butterworth 低通滤波 (零相位)."""
        from scipy.signal import butter, filtfilt

        N, D = actions.shape
        fs = 1.0 / self.dt if self.dt > 0 else 10.0
        nyquist = fs / 2.0
        cutoff = min(self.butter_cutoff, nyquist * 0.9)
        if cutoff <= 0:
            return actions.copy()

        b, a = butter(N=2, Wn=cutoff / nyquist, btype="low")
        result = np.empty_like(actions)
        for j in range(D):
            result[:, j] = filtfilt(b, a, actions[:, j])
        return result

    # ──────────────────────── Stage 3: 速度约束 ────────────────────────

    def _fit_interp_splines(self, actions: np.ndarray) -> list:
        """对 [N, D] 序列拟合插值 spline (s=0), 用于速度重采样.

        适用于 savgol/butter 等滤波后需要连续曲线的场景.
        """
        N, D = actions.shape
        t = np.arange(N, dtype=np.float64)
        splines = []
        for j in range(D):
            try:
                spl = splrep(t, actions[:, j], s=0)
                splines.append(spl)
            except Exception:
                splines.append(None)
        return splines

    def _resample_velocity_limit(
        self, actions: np.ndarray, splines: list | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """速度约束重采样: 使每步关节角变化不超过 v_max * dt.

        核心算法 — 时间缩放 (time scaling):
          1. 在 spline 曲线上计算速度轮廓 v(t) = max_j |dq_j/dt|
          2. 定义 time_cost(t) = max(1, v(t)/max_step)
             - v ≤ max_step: 保持原速 (1 个 dt 走 1 个参数单位)
             - v > max_step: 按比例拉伸时间 (减速)
          3. 积分得累积时间 T(t), 反演得 t(T)
          4. 在均匀 T = 0, 1, 2, ... 处采样 spline

        无 spline 时退化为逐段线性插值.

        Args:
            actions: [N, D] 平滑后的关节角序列
            splines: 每个Joint的 (tck) 元组列表, 用于 spline-aware 重采样

        Returns:
            (resampled, orig_indices): 重采样后的动作, 原始点在输出中的索引
        """
        N, D = actions.shape
        max_step = self.v_max * self.dt
        if max_step <= 0:
            return actions, np.arange(N)

        # 无 spline 可用时退化为逐段线性插值
        if splines is None or any(s is None for s in splines):
            return self._resample_velocity_limit_linear(actions, max_step)

        return self._resample_velocity_limit_time_scaling(actions, splines, max_step)

    def _resample_velocity_limit_time_scaling(
        self, actions: np.ndarray, splines: list, max_step: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """基于 spline 时间缩放的速度约束重采样.

        在 spline 曲线上计算连续速度轮廓, 通过时间缩放保证每步
        关节角变化 ≤ max_step, 且速度曲线光滑无突变.
        """
        N, D = actions.shape
        t_end = float(N - 1)

        # 1. 在细密网格上计算速度轮廓 (max over joints)
        N_fine = max(N * 20, 1000)
        t_fine = np.linspace(0, t_end, N_fine)

        vel_max = np.zeros(N_fine)
        for j in range(D):
            vel_j = np.abs(splev(t_fine, splines[j], der=1))
            vel_max = np.maximum(vel_max, vel_j)

        # 2. 时间开销: 每个 dt 步需要的参数推进量的倒数
        #    v ≤ max_step → time_cost = 1 (保持原速)
        #    v > max_step → time_cost = v/max_step (减速)
        time_cost = np.maximum(1.0, vel_max / max_step)

        # 3. 积分得累积时间 T(t) (单位: dt 步数)
        dt_fine = t_fine[1] - t_fine[0]
        T_cum = np.concatenate([[0.0], np.cumsum(time_cost[:-1] * dt_fine)])

        # 4. 在均匀 T = 0, 1, 2, ... 处采样
        T_total = T_cum[-1]
        n_output = int(np.ceil(T_total)) + 1
        T_output = np.arange(n_output, dtype=np.float64)

        # 5. 反演: T → t, 找到每个输出时间对应的参数值
        t_output = np.interp(T_output, T_cum, t_fine)
        t_output = np.clip(t_output, 0, t_end)

        # 确保终点精确
        if abs(t_output[-1] - t_end) > 1e-8:
            t_output = np.append(t_output, t_end)

        # 6. 在 spline 曲线上采样
        result = np.empty((len(t_output), D), dtype=np.float64)
        for j in range(D):
            result[:, j] = splev(t_output, splines[j])

        # 7. 定位原始帧在输出中的索引
        orig_indices = []
        for orig_t in range(N):
            idx = int(np.argmin(np.abs(t_output - float(orig_t))))
            orig_indices.append(idx)

        return result, np.array(orig_indices)

    def _resample_velocity_limit_linear(
        self, actions: np.ndarray, max_step: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """逐段线性插值重采样 (无 spline 时的 fallback)."""
        N, D = actions.shape

        new_steps = [actions[0]]
        orig_indices = [0]
        for t in range(N - 1):
            start = actions[t]
            end = actions[t + 1]
            diff = np.abs(end - start)
            max_ratio = np.max(diff) / max_step if np.max(diff) > 0 else 1.0
            if max_ratio > 1.0:
                n_insert = int(np.ceil(max_ratio)) - 1
                for k in range(1, n_insert + 1):
                    alpha = k / (n_insert + 1)
                    new_steps.append(start + alpha * (end - start))
            new_steps.append(end)
            orig_indices.append(len(new_steps) - 1)

        return np.array(new_steps, dtype=actions.dtype), np.array(orig_indices)

    # ──────────────────────── 便捷方法 ────────────────────────

    def smooth_full_action(self, action_14_2d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """对 [N, 14] 完整 action (joints + grippers) 做平滑.

        关节使用 spline/滤波 + 速度约束重采样;
        夹爪使用零阶保持 (zero-order hold) 对齐到平滑后的长度.

        Returns:
            (smoothed, orig_indices): 平滑后的 action 序列, 原始点在输出中的索引
        """
        if action_14_2d.shape[1] != 14:
            raise ValueError(f"Expected [N, 14], got {action_14_2d.shape}")

        joints = action_14_2d[:, JOINT_INDICES_14]   # [N, 12]
        grippers = action_14_2d[:, GRIPPER_INDICES_14]  # [N, 2]

        smoothed_joints, orig_indices = self.smooth(joints)
        M = len(smoothed_joints)

        # 夹爪: 零阶保持对齐
        # orig_indices[i] 是原始帧 i 在平滑轨迹中的索引,
        # 中间点保持前一个原始帧的夹爪值
        smoothed_grippers = np.zeros((M, 2), dtype=grippers.dtype)
        seg_idx = np.searchsorted(orig_indices, np.arange(M), side='right') - 1
        seg_idx = np.clip(seg_idx, 0, len(grippers) - 1)
        smoothed_grippers = grippers[seg_idx]

        result = np.zeros((M, 14), dtype=action_14_2d.dtype)
        result[:, JOINT_INDICES_14] = smoothed_joints
        result[:, GRIPPER_INDICES_14] = smoothed_grippers

        return result, orig_indices

    def smooth_chunk(
        self, action_3d: np.ndarray
    ) -> np.ndarray:
        """对模型原始输出 [C, F, action_per_frame] 做平滑.

        收集所有 step → 平滑 → 重组为相同形状.

        Args:
            action_3d: [14, F, P] 模型原始输出

        Returns:
            smoothed: [14, F, P'] 平滑后, P' 可能与 P 不同 (速度重采样)
        """
        C, F, P = action_3d.shape
        if C != 14:
            raise ValueError(f"Expected C=14, got {C}")

        # 收集所有 step → [F*P, 14]
        all_steps = np.empty((F * P, 14), dtype=np.float64)
        idx = 0
        for i in range(F):
            for j in range(P):
                all_steps[idx] = action_3d[:, i, j]
                idx += 1

        # 平滑
        smoothed, _ = self.smooth_full_action(all_steps)  # [M, 14]
        return smoothed

    # ──────────────────────── 可视化 ────────────────────────

    def plot_comparison(
        self,
        raw: np.ndarray,
        smoothed: np.ndarray,
        save_path: str,
        joint_names: list[str] | None = None,
    ):
        """画出 12 个关节角的 before/after 对比图.

        Args:
            raw: [N, 12] 原始关节角 (弧度)
            smoothed: [M, 12] 平滑后 (弧度)
            save_path: 图片保存路径
            joint_names: 12 个关节的名称列表
        """
        import matplotlib.pyplot as plt

        if joint_names is None:
            joint_names = [
                f"L_J{i}" for i in range(1, 7)
            ] + [f"R_J{i}" for i in range(1, 7)]

        fig, axes = plt.subplots(4, 3, figsize=(16, 12))
        axes = axes.flatten()

        t_raw = np.arange(len(raw)) * self.dt
        t_sm = np.arange(len(smoothed)) * self.dt

        for j in range(12):
            ax = axes[j]
            ax.plot(t_raw, np.degrees(raw[:, j]), "o-", ms=2, lw=0.8,
                    alpha=0.5, label="raw", color="tab:blue")
            ax.plot(t_sm, np.degrees(smoothed[:, j]), "-", lw=1.5,
                    label="smoothed", color="tab:red")
            ax.set_title(joint_names[j])
            ax.set_ylabel("deg")
            ax.legend(fontsize=7)
            if j >= 9:
                ax.set_xlabel("time (s)")

        fig.suptitle(
            f"Joint Trajectory Smoothing ({self.smooth_method})\n"
            f"smooth_factor={self.smooth_factor}, v_max={self.v_max}",
            fontsize=12,
        )
        fig.tight_layout()
        fig.savefig(save_path, dpi=150)
        plt.close(fig)
        print(f"  [Smoother] 对比图已保存: {save_path}")


# ──────────────────────── 测试: 离线对比 ────────────────────────

def demo_compare_npy(npy_path: str, output_dir: str = "./smooth_debug"):
    """从 action_joints.npy 加载数据, 做平滑并保存对比图.

    用法:
        python joint_trajectory_smoother.py --npy ./inference_logs/.../action_joints.npy
    """
    import os
    from pathlib import Path

    os.makedirs(output_dir, exist_ok=True)

    if isinstance(npy_path, str):
        data = np.load(npy_path)  # [N, 12]
        print(f"加载 {npy_path}: shape={data.shape}")
    else:
        data = npy_path

    data = data[..., :6] + data[..., 7:13] 
    for method in ["spline", "savgol", "butter"]:
        print(f"\n  测试 {method}...")
        smoother = JointTrajectorySmoother(
            smooth_method=method,
            smooth_factor=0.1,
            v_max=3.0,
            dt=0.1,
            outlier_threshold_deg=10.0,
        )
        smoothed, _ = smoother.smooth(data)
        print(f"    原始 {len(data)} 步 → 平滑后 {len(smoothed)} 步")

        save_path = str(Path(output_dir) / f"compare_{method}.png")
        smoother.plot_comparison(data, smoothed, save_path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Joint 轨迹平滑离线对比测试")
    parser.add_argument("--npy", type=str, required=True,
                        help="action_joints.npy 路径")
    parser.add_argument("--output_dir", type=str, default="./smooth_debug",
                        help="输出目录")
    parser.add_argument("--method", type=str, default="spline",
                        choices=["spline", "savgol", "butter", "none"])
    parser.add_argument("--smooth_factor", type=float, default=0.1)
    parser.add_argument("--v_max", type=float, default=3.0)
    parser.add_argument("--dt", type=float, default=0.1)
    parser.add_argument("--outlier_threshold", type=float, default=10.0)
    args = parser.parse_args()

    import os
    from pathlib import Path

    os.makedirs(args.output_dir, exist_ok=True)
    data = np.load(args.npy)
    print(f"加载 {args.npy}: shape={data.shape}")

    smoother = JointTrajectorySmoother(
        smooth_method=args.method,
        smooth_factor=args.smooth_factor,
        v_max=args.v_max,
        dt=args.dt,
        outlier_threshold_deg=args.outlier_threshold,
    )
    smoothed, _ = smoother.smooth(data)
    print(f"原始 {len(data)} 步 → 平滑后 {len(smoothed)} 步")

    save_path = str(Path(args.output_dir) / f"compare_{args.method}.png")
    smoother.plot_comparison(data, smoothed, save_path)