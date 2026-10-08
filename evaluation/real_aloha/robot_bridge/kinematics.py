"""Robot-only URDF FK and bounded local DLS; no planning or scene truth."""

import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .geometry import transform


class ArmFK:
    def __init__(self, urdf, joint_names, base="base_link", tip="link6"):
        self.names = list(joint_names)
        edges = {
            j.find("child").get("link"): j
            for j in ET.parse(urdf).getroot().findall("joint")
        }
        self.chain = []
        while tip != base:
            j = edges[tip]
            origin = j.find("origin")
            xyz = (
                np.fromstring(origin.get("xyz", "0 0 0"), sep=" ")
                if origin is not None
                else np.zeros(3)
            )
            rpy = (
                np.fromstring(origin.get("rpy", "0 0 0"), sep=" ")
                if origin is not None
                else np.zeros(3)
            )
            t = np.eye(4)
            t[:3, :3], t[:3, 3] = Rotation.from_euler("xyz", rpy).as_matrix(), xyz
            axis = j.find("axis")
            axis = (
                np.fromstring(axis.get("xyz"), sep=" ")
                if axis is not None
                else np.array([1.0, 0, 0])
            )
            self.chain.insert(0, (j.get("name"), j.get("type"), t, axis))
            tip = j.find("parent").get("link")
        moving = [n for n, kind, _, _ in self.chain if kind != "fixed"]
        if set(moving) != set(self.names):
            raise ValueError(f"URDF chain/arm joint mismatch: {moving}, {self.names}")

    def matrix(self, q):
        q = np.asarray(q, float)
        if q.shape != (len(self.names),) or not np.isfinite(q).all():
            raise ValueError("Invalid arm joint vector")
        values, t = dict(zip(self.names, q)), np.eye(4)
        for name, kind, origin, axis in self.chain:
            motion = np.eye(4)
            if kind in ("revolute", "continuous"):
                motion[:3, :3] = Rotation.from_rotvec(axis * values[name]).as_matrix()
            elif kind != "fixed":
                raise ValueError("Only fixed/revolute arm chain supported")
            t = t @ origin @ motion
        return t

    def bounded_target(self, q, limits, root, target):
        q = np.asarray(q, float)
        now = root @ self.matrix(q)
        goal = transform(target["position"], target["quaternion_wxyz"])
        translation = goal[:3, 3] - now[:3, 3]
        rotation = Rotation.from_matrix(goal[:3, :3] @ now[:3, :3].T).as_rotvec()

        def bounded(x, cap):
            return x * min(1.0, cap / max(np.linalg.norm(x), 1e-12))

        error = np.r_[bounded(translation, 0.02), bounded(rotation, 0.1)]
        jacobian = np.zeros((6, len(q)))
        for i in range(len(q)):
            shifted = q.copy()
            shifted[i] += 1e-5
            nxt = root @ self.matrix(shifted)
            jacobian[:3, i] = (nxt[:3, 3] - now[:3, 3]) / 1e-5
            jacobian[3:, i] = (
                Rotation.from_matrix(nxt[:3, :3] @ now[:3, :3].T).as_rotvec() / 1e-5
            )
        delta = jacobian.T @ np.linalg.solve(
            jacobian @ jacobian.T + 0.05**2 * np.eye(6), error
        )
        limits = np.asarray(limits, float)
        low, high = np.maximum(limits[:, 0], q - 0.05), np.minimum(
            limits[:, 1], q + 0.05
        )
        if np.any(low > high):
            raise ValueError("Joint outside bounded valid interval")
        result = np.clip(q + delta, low, high).astype(np.float32)
        return result, dict(
            target_position_error_m=float(np.linalg.norm(translation)),
            target_rotation_error_rad=float(np.linalg.norm(rotation)),
            proposed_joint_delta=(result - q).tolist(),
            bounded_task_delta=error.tolist(),
            method="robot_only_numerical_jacobian_dls",
            physical_tracking_verified=False,
        )
