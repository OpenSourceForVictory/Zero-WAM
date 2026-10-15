"""175 hardware binding and feedback checks around the imported 174 controller."""
from pathlib import Path
import sys
import time
from stop_request import StopRequested

import numpy as np
from piper_controller import PiperController as OriginalController

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from arm import channel_for, check_motion, feedback


class PiperController(OriginalController):
    def __init__(self, can_left=None, can_right=None):
        super().__init__(channel_for('left'), channel_for('right'), firmware_version='default')
        self.actuated = False
        self.stop_check = lambda: False
        self.max_target_delta_deg = None
        self.gripper_mode = 'legacy174'
        self.gripper_max = []

    def connect(self):
        try:
            super().connect()
            versions = self.read_firmware()
            if any(v is None or v.get('software_version') != 'S-V1.7-3' for v in versions):
                raise RuntimeError(f'175 expected S-V1.7-3 firmware, got {versions}')
            self.get_joint_angles()
            for grip in (self.gripper_l, self.gripper_r):
                param = grip.get_gripper_teaching_pendant_param()
                if param is None:
                    raise RuntimeError('Missing gripper range parameters')
                self.gripper_max.append(float(param.msg.max_range_config))
            print('175 firmware gripper maximums (m):', self.gripper_max)
        except BaseException:
            self.close()
            raise

    def get_joint_angles(self):
        return np.concatenate([feedback(a)[0].msg for a in (self.robot_l, self.robot_r)])

    def check_ready(self):
        if self.stop_check():
            raise StopRequested('Runtime stop requested')
        return np.concatenate([check_motion(a) for a in (self.robot_l, self.robot_r)])

    def enable(self):
        self.check_ready()  # Check both arms before enabling either.
        self.actuated = True
        for arm in (self.robot_l, self.robot_r):
            for _ in range(100):
                self.check_ready()
                if arm.enable():
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('Motor enable timeout')

    def validate_target(self, left, right):
        target = np.asarray([left, right], dtype=float)
        if target.shape != (2, 6) or not np.isfinite(target).all():
            raise ValueError('Expected finite 2x6 joint targets')
        current = self.check_ready().reshape(2, 6)
        delta = np.rad2deg(np.abs(target - current))
        if self.max_target_delta_deg is not None and delta.max() > self.max_target_delta_deg:
            raise RuntimeError(f'Target too far from current joints: {delta.max():.2f} deg > {self.max_target_delta_deg}; no automatic move to model start pose')

    def send_joints_with_gripper(self, left_joints, right_joints, left_gripper, right_gripper):
        self.validate_target(left_joints, right_joints)
        if not all(all(a.get_joints_enable_status_list()) for a in (self.robot_l, self.robot_r)):
            raise RuntimeError('Motor enable feedback lost')
        if not np.isfinite([left_gripper, right_gripper]).all():
            raise ValueError('Nonfinite gripper targets')
        self.robot_l.move_j(list(left_joints))
        self.robot_r.move_j(list(right_joints))
        if self.gripper_mode == 'legacy174':
            # Preserve the original 0.08 m cap and enforce this pair's own
            # firmware limits. Scaling (0.8/0.9) stays in the original loop.
            for grip, target, maximum in zip((self.gripper_l, self.gripper_r),
                    (left_gripper, right_gripper), self.gripper_max):
                grip.move_gripper_m(max(0.0, min(.08, maximum, target)), force=1.0)

    # Inherit 174 wait_both_motion_done unchanged: status OR angle tolerance,
    # return False on timeout/stall. The original loop continues on False.

    def emergency_stop(self):
        # No reset or disable: those can immediately drop the arms.
        for arm in (self.robot_l, self.robot_r):
            if arm is not None:
                try:
                    arm.electronic_emergency_stop()
                except Exception as exc:
                    print('Electronic stop failed:', exc)
        print('Electronic stop requested; damped descent is possible.')

    def close(self):
        for arm in (self.robot_l, self.robot_r):
            if arm is not None:
                arm.disconnect()
        self._connected = False
