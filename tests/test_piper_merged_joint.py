"""Exercise the copied client without ROS, a server, or CAN access."""
import importlib.util
from pathlib import Path
import sys
import types

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "evaluation/real_aloha/merged_client"


@pytest.mark.parametrize("smooth", [False, True])
def test_full_chunk_joint_mapping_and_cache(monkeypatch, tmp_path, smooth):
    def stub(name, **attrs):
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    stub("piper_controller", PiperController=object)
    stub("camera_manager", CameraManager=object)
    stub("va_client", WebsocketClientPolicy=object)
    stub("realtime_visualizer", InferenceStatus=types.SimpleNamespace)
    stub("cv2", COLOR_BGR2RGB=1, cvtColor=lambda x, _: x[..., ::-1], imwrite=lambda *a: True)
    spec = importlib.util.spec_from_file_location("joint_trajectory_smoother", CLIENT / "joint_trajectory_smoother.py")
    smoother_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, smoother_module)
    spec.loader.exec_module(smoother_module)
    spec = importlib.util.spec_from_file_location("merged_joint_test", CLIENT / "real_robot_inference_merged.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "_check_emergency_stop", lambda: False)

    # Distinct channels detect swapped arms and accidentally using EEF values.
    action = np.zeros((28, 2, 16))
    action[:14] = 999
    action[14:26] = np.arange(12)[:, None, None] * .01
    action[26:] = .06
    sent, cached, resets = [], [], []
    piper = types.SimpleNamespace(
        get_joint_angles=lambda: np.zeros(12),
        send_joints_with_gripper=lambda *args: sent.append(args),
        # Original loop continues on timeout; preserve and document this behavior.
        wait_both_motion_done=lambda **kwargs: False,
    )
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    camera = types.SimpleNamespace(get_frames=lambda: (frame, frame, frame))
    client = types.SimpleNamespace(
        reset=lambda prompt: resets.append(prompt),
        infer=lambda msg: {"action": action.copy()},
        compute_kv_cache=lambda obs, raw: cached.append((obs, raw)),
    )
    logger = types.SimpleNamespace(
        save_dir=tmp_path / "run", start_time=module.time.time(),
        log_server_infer=lambda _: None, log_kv_cache=lambda _: None,
        log_key_frame=lambda *args: None,
    )
    cfg = module.MoveJConfig(prompt="test", max_chunks=1, save_dir=str(tmp_path))
    smoother = smoother_module.JointTrajectorySmoother(
        smooth_method=cfg.smooth_method, smooth_factor=cfg.smooth_factor,
        v_max=cfg.v_max, dt=cfg.action_dt,
    ) if smooth else None
    count = module._inference_loop(piper, camera, client, cfg, logger, smoother)
    assert count == len(sent) == 16  # First 16 padded steps were skipped.
    for left, right, lg, rg in sent:
        np.testing.assert_allclose(left, np.arange(6) * .01, atol=1e-10)
        np.testing.assert_allclose(right, np.arange(6, 12) * .01, atol=1e-10)
        assert lg == pytest.approx(.048)
        assert rg == pytest.approx(.054)
    assert resets == ["test"]
    assert len(cached) == 1 and len(cached[0][0]) == 4
    np.testing.assert_array_equal(cached[0][1], action)
