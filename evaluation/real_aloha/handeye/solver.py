"""All transforms use A_from_B: p_A = A_from_B @ p_B; distances in metres."""
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation


def rigid(value):
    t = np.asarray(value, dtype=float)
    if (t.shape != (4, 4) or not np.isfinite(t).all()
            or not np.allclose(t[3], [0, 0, 0, 1], atol=1e-7)
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(t[:3, :3]), 1, atol=1e-5)):
        raise ValueError("Expected a finite rigid 4x4 transform")
    return t


def transform(rotation, translation):
    t = np.eye(4)
    t[:3, :3] = rotation
    t[:3, 3] = np.asarray(translation).reshape(3)
    return rigid(t)


def effective_poses(samples, mode):
    if mode not in ("eye_in_hand", "eye_to_hand"):
        raise ValueError("Unknown calibration mode")
    robot = [rigid(s["base_from_ee"]) for s in samples]
    camera = [rigid(s["camera_from_target"]) for s in samples]
    if mode == "eye_to_hand":
        robot = [np.linalg.inv(t) for t in robot]
    return robot, camera


def fit(samples, mode):
    if len(samples) < 10:
        raise ValueError("Need at least 10 samples for fitting")
    robot, camera = effective_poses(samples, mode)
    # Multiple nonparallel rotation axes are necessary for observability.
    motions = np.array([Rotation.from_matrix(robot[0][:3, :3].T @ t[:3, :3]).as_rotvec()
                        for t in robot[1:]])
    singular = np.linalg.svd(motions, compute_uv=False)
    if singular[1] < 0.1:
        raise ValueError("Insufficient rotation diversity: rotate about multiple axes")
    r, t = cv2.calibrateHandEye(
        [x[:3, :3] for x in robot], [x[:3, 3] for x in robot],
        [x[:3, :3] for x in camera], [x[:3, 3] for x in camera],
        method=cv2.CALIB_HAND_EYE_PARK)
    return transform(r, t)


def constants(samples, mode, solution):
    robot, camera = effective_poses(samples, mode)
    return [a @ solution @ b for a, b in zip(robot, camera)]


def mean_pose(poses):
    return transform(Rotation.from_matrix(np.array([p[:3, :3] for p in poses])).mean().as_matrix(),
                     np.mean([p[:3, 3] for p in poses], axis=0))


def residuals(poses, reference):
    mm = np.array([np.linalg.norm(p[:3, 3] - reference[:3, 3]) * 1000 for p in poses])
    deg = np.array([np.degrees(Rotation.from_matrix(reference[:3, :3].T @ p[:3, :3]).magnitude())
                    for p in poses])
    return {"translation_rms_mm": float(np.sqrt(np.mean(mm ** 2))),
            "translation_max_mm": float(mm.max()),
            "rotation_rms_deg": float(np.sqrt(np.mean(deg ** 2))),
            "rotation_max_deg": float(deg.max())}


def solve(dataset):
    samples, mode = dataset["samples"], dataset["mode"]
    if len(samples) < 15:
        raise ValueError("Need at least 15 samples (including held-out validation)")
    held_out = samples[::5]
    training = [s for i, s in enumerate(samples) if i % 5]
    preliminary = fit(training, mode)
    reference = mean_pose(constants(training, mode, preliminary))
    validation = residuals(constants(held_out, mode, preliminary), reference)
    final = fit(samples, mode)
    poses = constants(samples, mode, final)
    key = "ee_from_camera" if mode == "eye_in_hand" else "base_from_camera"
    return {"schema_version": 1, "mode": mode, "units": "metres",
            "convention": "A_from_B maps coordinates from B to A",
            "verified": False, "sample_count": len(samples),
            "metadata": dataset.get("metadata", {}), key: final.tolist(),
            "fit_residual": residuals(poses, mean_pose(poses)),
            "held_out_residual": validation, "held_out_count": len(held_out),
            "note": "Residuals measure consistency, not absolute accuracy. Verify on hardware."}


def save_json(path, value):
    # Exclusive creation protects existing sessions and measured results.
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write("\n")


def combine_bases(left, right):
    """Use a shared fixed camera to express both arm bases in the left base."""
    for result in (left, right):
        if result.get("mode") != "eye_to_hand" or result.get("units") != "metres":
            raise ValueError("Both results must be eye_to_hand calibrations in metres")
    lm, rm = left.get("metadata", {}), right.get("metadata", {})
    lf, rf = lm.get("frames", []), rm.get("frames", [])
    if len(lf) != 2 or len(rf) != 2 or not lf[1] or lf[1] != rf[1]:
        raise ValueError("Both calibrations must use the same camera optical frame")
    # Same frame name alone does not establish physical camera identity.
    if not lm.get("image_topic") or lm.get("image_topic") != rm.get("image_topic"):
        raise ValueError("Both calibrations must use the same camera image topic")
    if not lm.get("intrinsics") or lm.get("intrinsics") != rm.get("intrinsics"):
        raise ValueError("Camera intrinsics must match between sessions")
    if not lm.get("pose_topic") or not rm.get("pose_topic") or lm["pose_topic"] == rm["pose_topic"]:
        raise ValueError("Use separate left and right arm pose topics")
    left_from_camera = rigid(left["base_from_camera"])
    right_from_camera = rigid(right["base_from_camera"])
    left_from_right = rigid(left_from_camera @ np.linalg.inv(right_from_camera))
    return {"schema_version": 1, "verified": False, "units": "metres",
            "convention": "A_from_B maps coordinates from B to A",
            "common_base": "left_arm_base", "arm_order": ["left", "right"],
            "left_from_right": left_from_right.tolist(),
            "right_from_left": np.linalg.inv(left_from_right).tolist(),
            "base_transforms": [np.eye(4).tolist(), left_from_right.tolist()],
            "source_metadata": {"left": lm, "right": rm},
            "source_held_out_residuals": {"left": left.get("held_out_residual"),
                                          "right": right.get("held_out_residual")},
            "note": "Requires the same physical camera, unchanged camera/base mounts and lift height. "
                    "Verify independently before using base_transforms. This is not a full robot config."}
