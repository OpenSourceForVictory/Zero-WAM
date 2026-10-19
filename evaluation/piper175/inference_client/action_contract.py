"""Translate server actions into the original client's interleaved layout."""
import numpy as np

CONTRACT = "piperpika_absolute_joint12_gripper2_v1"
ORDER = [f"{side}_joint_{i}" for side in ("left", "right") for i in range(6)] + ["left_gripper", "right_gripper"]


def validate_reset(reply, mode):
    metadata = reply if isinstance(reply, dict) else {}
    advertised = metadata.get("action_contract")
    if mode == "piperpika":
        if advertised != CONTRACT or metadata.get("action_order") != ORDER:
            raise ValueError("Server reset does not match PiperPika joint12 + gripper2 contract/order")
    elif advertised is not None:
        raise ValueError("Server advertises an action contract; select its explicit client adapter")


def execution_tensor(action, mode):
    a = np.asarray(action)
    if a.ndim != 3 or a.shape[0] not in (14, 28) or min(a.shape[1:]) <= 0 or not np.isfinite(a).all():
        raise ValueError(f"Invalid action tensor: {a.shape}")
    if a.shape[2] % 4:
        raise ValueError("Actions per latent frame must be divisible by four")
    if mode == "piperpika":
        if a.shape[0] != 14:
            raise ValueError("PiperPika joint12 + gripper2 requires exactly 14 channels")
        return a[[0, 1, 2, 3, 4, 5, 12, 6, 7, 8, 9, 10, 11, 13]].copy()
    if a.shape[0] == 28:
        return a[list(range(14, 20)) + [26] + list(range(20, 26)) + [27]].copy()
    return a.copy()


def gripper_targets(left, right, mode):
    if mode == "piperpika":
        return left, right
    left, right = left * 0.80, right * 0.90
    return (0 if left <= 0.02 else left, 0 if right <= 0.02 else right)
