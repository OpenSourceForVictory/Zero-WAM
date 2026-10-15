"""
Piper 双臂机械臂控制器 (pyAgxArm 版本)
运行环境: piper_pyagxarm

封装 pyAgxArm SDK，提供安全的双臂使能/运动/失能功能，
确保异常时能自动复位。

注意: 单位变更
- get_joint_angles() 返回弧度 (旧版本返回毫弧度)
- get_end_pose() 返回米 + 弧度 (保持不变)
- 所有 send_* 方法接受弧度 (保持不变)
"""

import time
import numpy as np
from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel, PiperFW


class PiperController:
    """双臂控制器，支持安全复位。"""

    def __init__(self, can_left="can_left", can_right="can_right", firmware_version="default"):
        """
        初始化双臂控制器。

        Args:
            can_left: 左臂 CAN 接口名称 (如 "can_left")
            can_right: 右臂 CAN 接口名称 (如 "can_right")
            firmware_version: 固件版本 ("default", "v183", "v188")
                - "default": firmware ≤ S-V1.8-2
                - "v183": firmware S-V1.8-3 ~ S-V1.8-7
                - "v188": firmware ≥ S-V1.8-8
        """
        self.can_left = can_left
        self.can_right = can_right
        self.firmware_version = firmware_version

        self.robot_l = None
        self.robot_r = None
        self.gripper_l = None
        self.gripper_r = None
        self._connected = False

    def connect(self):
        """连接两个机械臂。"""
        # 创建左臂配置和实例
        config_l = create_agx_arm_config(
            robot=ArmModel.PIPER,
            firmeware_version=self.firmware_version,
            interface="socketcan",
            channel=self.can_left,
        )
        self.robot_l = AgxArmFactory.create_arm(config_l)

        # 创建右臂配置和实例
        config_r = create_agx_arm_config(
            robot=ArmModel.PIPER,
            firmeware_version=self.firmware_version,
            interface="socketcan",
            channel=self.can_right,
        )
        self.robot_r = AgxArmFactory.create_arm(config_r)

        # 连接
        self.robot_l.connect()
        time.sleep(0.3)
        self.robot_r.connect()
        time.sleep(0.3)

        # 初始化夹爪
        self.gripper_l = self.robot_l.init_effector(self.robot_l.OPTIONS.EFFECTOR.AGX_GRIPPER)
        self.gripper_r = self.robot_r.init_effector(self.robot_r.OPTIONS.EFFECTOR.AGX_GRIPPER)

        self._connected = True
        print(f"[Piper] 已连接: {self.can_left} (左臂), {self.can_right} (右臂)")

    def read_firmware(self):
        """读取固件版本。"""
        time.sleep(0.05)
        fw_l = self.robot_l.get_firmware(timeout=1.0)
        fw_r = self.robot_r.get_firmware(timeout=1.0)
        return (fw_l, fw_r)

    def check_slave_mode(self, timeout=1.0):
        """检查是否处于从臂模式 (关节反馈 Hz > 0)。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            ja_l = self.robot_l.get_joint_angles()
            ja_r = self.robot_r.get_joint_angles()
            if (ja_l is not None and ja_l.hz > 5) and (ja_r is not None and ja_r.hz > 5):
                return True
            time.sleep(0.1)
        return False

    def set_slave_mode(self):
        """发送从臂模式指令 (需要断电重启后生效)。"""
        self.robot_l.set_follower_mode()
        self.robot_r.set_follower_mode()
        print("[Piper] 从臂模式指令已发送，请断电重启机械臂。")

    def enable(self):
        """使能双臂。"""
        for name, robot in [("左臂", self.robot_l), ("右臂", self.robot_r)]:
            for i in range(200):
                if robot.enable():
                    print(f"[Piper] {name} 使能成功")
                    break
                time.sleep(0.01)
            else:
                print(f"[Piper] {name} 使能超时!")

    def disable(self):
        """失能双臂 (安全操作)。"""
        for name, robot in [("左臂", self.robot_l), ("右臂", self.robot_r)]:
            for _ in range(50):
                if robot.disable():
                    print(f"[Piper] {name} 已失能")
                    break
                time.sleep(0.03)

    def go_zero(self, duration=5.0):
        """控制双臂回到零位。

        Args:
            duration: 归零运动时间 (秒), 默认 5 秒
        """
        dt = 0.01
        zero_joints = [0.0] * 6

        # 持续发送零位指令
        for _ in range(int(duration / dt)):
            self.robot_l.move_j(zero_joints)
            self.robot_r.move_j(zero_joints)
            self.gripper_l.move_gripper_m(0.0, force=1.0)  # 关闭夹爪
            self.gripper_r.move_gripper_m(0.0, force=1.0)
            time.sleep(dt)

        print("[Piper] 双臂已归零")

    def get_joint_angles(self):
        """
        读取当前关节角度，返回 numpy array [12]。

        Returns:
            numpy array [12]: [左臂j1..j6, 右臂j1..j6]，单位为弧度

        注意: 与旧版本不同，此版本返回弧度而非毫弧度。
        """
        ja_l = self.robot_l.get_joint_angles()
        ja_r = self.robot_r.get_joint_angles()

        if ja_l is None or ja_r is None:
            # 数据尚未到达时返回零数组
            return np.zeros(12)

        # ja.msg 是 [j1, j2, j3, j4, j5, j6] 弧度列表
        return np.concatenate([ja_l.msg, ja_r.msg])

    def get_end_pose(self):
        """
        读取当前末端位姿 (法兰坐标系).

        Returns: (left_pose, right_pose)
        每个 pose 为 [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
        - 位置单位: 米
        - 旋转单位: 弧度 (euler angles, xyz extrinsic)

        注意: 这返回的是法兰面位姿, 不包含 TCP 偏移。
        如果需要工具中心点位姿, 请使用 get_tcp_pose()。
        """
        fp_l = self.robot_l.get_flange_pose()
        fp_r = self.robot_r.get_flange_pose()

        if fp_l is None or fp_r is None:
            return [0.0] * 6, [0.0] * 6

        # fp.msg 是 [x, y, z, roll, pitch, yaw] 米+弧度列表
        return list(fp_l.msg), list(fp_r.msg)

    def get_tcp_pose(self):
        """
        读取当前 TCP (Tool Center Point) 位姿.

        TCP 位姿 = 法兰位姿 + TCP 偏移变换。
        如果未设置 TCP 偏移, 则与法兰位姿相同。

        Returns: (left_tcp_pose, right_tcp_pose)
        每个 pose 为 [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
        """
        tcp_l = self.robot_l.get_tcp_pose()
        tcp_r = self.robot_r.get_tcp_pose()

        if tcp_l is None or tcp_r is None:
            return [0.0] * 6, [0.0] * 6

        return list(tcp_l.msg), list(tcp_r.msg)

    def set_tcp_offset(self, left_tcp=None, right_tcp=None):
        """
        设置 TCP 偏移量 (相对于法兰坐标系).

        TCP 偏移定义了工具中心点相对于法兰面原点的位置和姿态。
        设置后, get_tcp_pose() 返回的位姿将包含此偏移,
        move_p() 仍然接受法兰位姿。

        Args:
            left_tcp: 左臂 TCP 偏移 [x, y, z, roll, pitch, yaw]
                - x, y, z: 偏移位置 (米), 法兰坐标系下
                - roll, pitch, yaw: 偏移姿态 (弧度, xyz extrinsic)
                - None 表示不修改左臂
            right_tcp: 右臂 TCP 偏移, 格式同上

        示例:
            # 设置左臂 TCP 偏移: 法兰下方 0.10m (典型夹爪长度)
            piper.set_tcp_offset(left_tcp=[0, 0, 0.10, 0, 0, 0])
        """
        if left_tcp is not None:
            self.robot_l.set_tcp_offset(left_tcp)
            print(f"[Piper] 左臂 TCP 偏移已设置: {left_tcp}")
        if right_tcp is not None:
            self.robot_r.set_tcp_offset(right_tcp)
            print(f"[Piper] 右臂 TCP 偏移已设置: {right_tcp}")

    def flange_to_tcp(self, left_flange, right_flange):
        """
        将法兰位姿转换为 TCP 位姿。

        Args:
            left_flange/right_flange: [x, y, z, roll, pitch, yaw] 米+弧度

        Returns: (left_tcp, right_tcp) 米+弧度
        """
        left_tcp = self.robot_l.get_flange2tcp_pose(list(left_flange))
        right_tcp = self.robot_r.get_flange2tcp_pose(list(right_flange))
        return left_tcp, right_tcp

    def tcp_to_flange(self, left_tcp, right_tcp):
        """
        将 TCP 位姿转换为法兰位姿。

        将转换后的法兰位姿传给 move_p() 可使 TCP 移动到目标 TCP 位姿。

        Args:
            left_tcp/right_tcp: [x, y, z, roll, pitch, yaw] 米+弧度

        Returns: (left_flange, right_flange) 米+弧度
        """
        left_flange = self.robot_l.get_tcp2flange_pose(list(left_tcp))
        right_flange = self.robot_r.get_tcp2flange_pose(list(right_tcp))
        return left_flange, right_flange

    def send_end_pose(self, left_pose, right_pose):
        """
        发送末端位姿指令 (使用 MOVE_P 模式, 即末端点控制).

        注意: 此方法接受法兰位姿。如果需要发送 TCP 位姿,
        请使用 send_tcp_pose()。

        left_pose/right_pose: [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
        单位: 米 (平移), 弧度 (euler angles, xyz extrinsic)
        """
        self.robot_l.move_p(list(left_pose))
        self.robot_r.move_p(list(right_pose))

    def send_tcp_pose(self, left_tcp, right_tcp):
        """
        发送 TCP 位姿指令 (自动转换为法兰位姿后发送).

        此方法会先将 TCP 位姿转换为法兰位姿, 然后调用 move_p()。
        前提: 必须先调用 set_tcp_offset() 设置 TCP 偏移。

        Args:
            left_tcp/right_tcp: [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
                目标 TCP 位姿

        示例:
            # 设置 TCP 偏移 (夹爪指尖中心)
            piper.set_tcp_offset(left_tcp=[0, 0, 0.12, 0, 0, 0])
            # 发送 TCP 目标位姿
            piper.send_tcp_pose([0.3, 0, 0.2, 0, 1.57, 0], [0.3, 0, 0.2, 0, 1.57, 0])
        """
        left_flange = self.robot_l.get_tcp2flange_pose(list(left_tcp))
        right_flange = self.robot_r.get_tcp2flange_pose(list(right_tcp))
        self.robot_l.move_p(left_flange)
        self.robot_r.move_p(right_flange)

    def send_end_pose_with_gripper(self, left_pose, right_pose, left_gripper_val, right_gripper_val):
        """
        同时发送末端位姿和夹爪指令 (法兰位姿)。

        Args:
            left_pose/right_pose: [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
            left_gripper_val/right_gripper_val: 夹爪开口宽度，单位米
                - 0.0 = 完全关闭
                - 0.08 = 完全打开 (Piper 夹爪最大行程约 80mm)
        """
        self.robot_l.move_p(list(left_pose))
        self.robot_r.move_p(list(right_pose))

        # 夹爪值已经是米单位，直接传递
        # clamp 到有效范围 [0, 0.08]
        left_gripper_val = max(0.0, min(0.08, left_gripper_val))
        right_gripper_val = max(0.0, min(0.08, right_gripper_val))

        self.gripper_l.move_gripper_m(left_gripper_val, force=1.0)
        self.gripper_r.move_gripper_m(right_gripper_val, force=1.0)

    def send_tcp_pose_with_gripper(self, left_tcp, right_tcp, left_gripper_val, right_gripper_val):
        """
        同时发送 TCP 位姿和夹爪指令。

        将 TCP 位姿转换为法兰位姿后发送, 并控制夹爪。

        Args:
            left_tcp/right_tcp: [x_m, y_m, z_m, roll_rad, pitch_rad, yaw_rad]
                目标 TCP 位姿
            left_gripper_val/right_gripper_val: 夹爪开口宽度，单位米
        """
        left_flange = self.robot_l.get_tcp2flange_pose(list(left_tcp))
        right_flange = self.robot_r.get_tcp2flange_pose(list(right_tcp))

        self.robot_l.move_p(left_flange)
        self.robot_r.move_p(right_flange)

        left_gripper_val = max(0.0, min(0.08, left_gripper_val))
        right_gripper_val = max(0.0, min(0.08, right_gripper_val))

        self.gripper_l.move_gripper_m(left_gripper_val, force=1.0)
        self.gripper_r.move_gripper_m(right_gripper_val, force=1.0)

    def send_joint_angles(self, left_joints, right_joints):
        """
        发送关节角控制指令 (使用 MOVE_J 模式)。

        Args:
            left_joints/right_joints: [j1, j2, j3, j4, j5, j6] in radians
        """
        self.robot_l.move_j(list(left_joints))
        self.robot_r.move_j(list(right_joints))

    @staticmethod
    def _check_joint_reached(current_angles, target_joints, tol_rad=0.05):
        """检查关节角是否已到达目标 (容差兜底, 防止 motion_status 卡在 1).

        Args:
            current_angles: 当前关节角 (list/ndarray), 由调用方预取
            target_joints: 目标关节角 (list/ndarray)
            tol_rad: 容差 (弧度)
        """
        current = np.array(current_angles, dtype=np.float64)
        target = np.array(target_joints, dtype=np.float64)
        return np.max(np.abs(current - target)) < tol_rad

    def _wait_motion_done(self, robot, target_joints=None, timeout=5.0,
                          poll_interval=0.05, tol_rad=0.05):
        """等待单臂运动到位.

        判断条件 (满足任一即认为到位):
        1. motion_status == 0 (控制器报告到达目标)
        2. 关节角误差 < tol_rad (兜底, 防止控制器一直报 FAILED)
        """
        start_t = time.monotonic()
        ja = None
        while True:
            status = robot.get_arm_status()
            if status is not None and getattr(status.msg, "motion_status", None) == 0:
                return True
            if target_joints is not None:
                ja = robot.get_joint_angles()
                if ja is not None and self._check_joint_reached(ja.msg, target_joints, tol_rad):
                    return True
            if time.monotonic() - start_t > timeout:
                if target_joints is not None and ja is not None:
                    err = np.max(np.abs(np.array(ja.msg) - np.array(target_joints)))
                    print(f"[Piper] wait_motion_done 超时 ({timeout:.1f}s), "
                          f"最大关节误差: {np.degrees(err):.2f}°")
                else:
                    print(f"[Piper] wait_motion_done 超时 ({timeout:.1f}s)")
                return False
            time.sleep(poll_interval)

    def wait_both_motion_done(self, left_joints=None, right_joints=None,
                              timeout=5.0, poll_interval=0.05, tol_rad=0.02,
                              stale_timeout=0.5, stale_tol=0.005):
        """同时等待双臂运动到位 (并行轮询，共享超时).

        判断条件: motion_status==0 或 关节角误差 < tol_rad.
        新增: 如果 stale_timeout 内关节角无变化, 也判定超时.
        """
        start_t = time.monotonic()
        left_done = False
        right_done = False

        stale_start_l = None  # 第一次检测到关节角无变化的时刻
        stale_start_r = None
        last_ja_l = None
        last_ja_r = None

        while not (left_done and right_done):
            # 每臂只取一次关节角，复用给到达判断 + stale + 超时打印
            ja_l = self.robot_l.get_joint_angles() if not left_done else None
            ja_r = self.robot_r.get_joint_angles() if not right_done else None

            if not left_done:
                status_l = self.robot_l.get_arm_status()
                reached_l = (left_joints is not None and ja_l is not None
                             and self._check_joint_reached(ja_l.msg, left_joints, tol_rad))
                if status_l.msg.mode_feedback == 0x1:
                    if status_l is not None and (getattr(status_l.msg, "motion_status", None) == 0 or reached_l):
                        left_done = True
                else:
                    if reached_l:
                        left_done = True
            if not right_done:
                status_r = self.robot_r.get_arm_status()
                reached_r = (right_joints is not None and ja_r is not None
                             and self._check_joint_reached(ja_r.msg, right_joints, tol_rad))
                if status_r.msg.mode_feedback == 0x1:
                    if status_r is not None and (getattr(status_r.msg, "motion_status", None) == 0 or reached_r):
                        right_done = True
                else:
                    if reached_r:
                        right_done = True
            if left_done and right_done:
                return True

            # stale detection: 第一次检测到无变化时记录时刻, 超时仍未恢复则判定卡死
            now = time.monotonic()
            if not left_done and ja_l is not None:
                if last_ja_l is not None:
                    if np.max(np.abs(np.array(ja_l.msg) - np.array(last_ja_l))) < stale_tol:
                        if stale_start_l is None:
                            stale_start_l = now
                    else:
                        stale_start_l = None
                last_ja_l = ja_l.msg

            if not right_done and ja_r is not None:
                if last_ja_r is not None:
                    if np.max(np.abs(np.array(ja_r.msg) - np.array(last_ja_r))) < stale_tol:
                        if stale_start_r is None:
                            stale_start_r = now
                    else:
                        stale_start_r = None
                last_ja_r = ja_r.msg

            stale_l = stale_start_l is not None and now - stale_start_l > stale_timeout
            stale_r = stale_start_r is not None and now - stale_start_r > stale_timeout
            if stale_l or stale_r:
                errs = []
                if stale_l and left_joints is not None and ja_l is not None:
                    diff = np.abs(np.array(ja_l.msg) - np.array(left_joints))
                    max_idx = np.argmax(diff)
                    errs.append(f"左臂关节{max_idx+1}误差{np.degrees(diff[max_idx]):.2f}°")
                if stale_r and right_joints is not None and ja_r is not None:
                    diff = np.abs(np.array(ja_r.msg) - np.array(right_joints))
                    max_idx = np.argmax(diff)
                    errs.append(f"右臂关节{max_idx+1}误差{np.degrees(diff[max_idx]):.2f}°")
                err_info = ", ".join(errs) if errs else ""
                stuck = ["左臂" if stale_l else "右臂" if stale_r else ""]
                stuck = [s for s in stuck if s]  # 过滤空串
                print(f"[Piper] wait_both_motion_done 卡住超时 "
                      f"(关节角 {stale_timeout:.1f}s 无变化, "
                      f"{'/'.join(stuck)}卡住, {err_info})")
                return False

            if time.monotonic() - start_t > timeout:
                errs = []
                if left_joints is not None and ja_l is not None:
                    diff = np.abs(np.array(ja_l.msg) - np.array(left_joints))
                    max_idx = np.argmax(diff)
                    errs.append(f"左臂关节{max_idx+1}误差最大{np.degrees(diff[max_idx]):.2f}°")
                if right_joints is not None and ja_r is not None:
                    diff = np.abs(np.array(ja_r.msg) - np.array(right_joints))
                    max_idx = np.argmax(diff)
                    errs.append(f"右臂关节{max_idx+1}误差最大{np.degrees(diff[max_idx]):.2f}°")
                err_info = ", ".join(errs) if errs else ""
                print(f"[Piper] wait_both_motion_done 超时 ({timeout:.1f}s, "
                      f"左臂={'到位' if left_done else '未到位'}, "
                      f"右臂={'到位' if right_done else '未到位'}"
                      f"{', ' + err_info if err_info else ''})")
                return False
            time.sleep(poll_interval)

    def send_joints_with_gripper_movejs(self, left_joints, right_joints,
                                left_gripper, right_gripper):
        """发送关节角和夹爪指令到 Piper (非侵入式封装)."""
        self.robot_l.move_js(list(left_joints))
        self.robot_r.move_js(list(right_joints))
        # 夹爪 clamp 到硬件有效范围 [0, 0.08]m
        left_gripper = max(0.0, min(0.08, left_gripper))
        right_gripper = max(0.0, min(0.08, right_gripper))
        self.gripper_l.move_gripper_m(left_gripper, force=1.0)
        self.gripper_r.move_gripper_m(right_gripper, force=1.0)    

    def send_joints_with_gripper(self, left_joints, right_joints,
                                left_gripper, right_gripper):
        """发送关节角和夹爪指令到 Piper (非侵入式封装)."""
        self.robot_l.move_j(list(left_joints))
        self.robot_r.move_j(list(right_joints))
        # 夹爪 clamp 到硬件有效范围 [0, 0.08]m
        left_gripper = max(0.0, min(0.08, left_gripper))
        right_gripper = max(0.0, min(0.08, right_gripper))
        self.gripper_l.move_gripper_m(left_gripper, force=1.0)
        self.gripper_r.move_gripper_m(right_gripper, force=1.0)  

    def send_joints_with_gripper_blocking(self, left_joints, right_joints,
                                           left_gripper, right_gripper,
                                           timeout=5.0, poll_interval=0.01):
        """发送关节角和夹爪指令，等待双臂运动到位后返回。"""
        self.robot_l.move_j(list(left_joints))
        self.robot_r.move_j(list(right_joints))
        left_gripper = max(0.0, min(0.08, left_gripper))
        right_gripper = max(0.0, min(0.08, right_gripper))
        self.gripper_l.move_gripper_m(left_gripper, force=1.0)
        self.gripper_r.move_gripper_m(right_gripper, force=1.0)
        
        self._wait_motion_done(self.robot_l, timeout, poll_interval=poll_interval)
        self._wait_motion_done(self.robot_r, timeout, poll_interval=poll_interval)

    def emergency_stop(self):
        """紧急停止: 立即电子急停, 然后复位。"""
        print("[Piper] !!! 紧急停止 !!!")
        try:
            self.robot_l.electronic_emergency_stop()
            self.robot_r.electronic_emergency_stop()
            time.sleep(0.5)
            self.robot_l.reset()
            self.robot_r.reset()
            self.disable()
        except Exception as e:
            print(f"[Piper] 紧急停止时出错: {e}")

    def close(self):
        """关闭连接: 归零到安全位置 (不自动失能)。"""
        if self._connected:
            print("[Piper] 正在归零...")
            # self.go_zero()
            self.robot_l.disconnect()
            self.robot_r.disconnect()
            self.robot_l = None
            self.robot_r = None
            self.gripper_l = None
            self.gripper_r = None
            self._connected = False
