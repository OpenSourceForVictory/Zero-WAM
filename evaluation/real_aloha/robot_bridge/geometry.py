"""Robot-only FK and bounded edits; never reads objects or advances physics."""

from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def transform(position, quaternion_wxyz):
    q = np.asarray(quaternion_wxyz, float)
    t = np.eye(4)
    t[:3, :3] = Rotation.from_quat(q[[1, 2, 3, 0]]).as_matrix()
    t[:3, 3] = position
    return t


def pose(t):
    q = Rotation.from_matrix(t[:3, :3]).as_quat()
    return dict(position=t[:3, 3].tolist(), quaternion_wxyz=q[[3, 0, 1, 2]].tolist())
