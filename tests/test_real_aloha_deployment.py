"""Real deployment contracts; no ROS installation or checkpoint required."""

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest
from test_real_aloha_action_adapter import _FakePolicy, _FakeRobot
from evaluation.real_aloha.run_policy import parse_args, run_episode
from evaluation.real_aloha.robot_bridge.server import ReadOnlySession
from evaluation.real_aloha.robot_bridge.piper_ros import PiperRosBackend
from evaluation.real_aloha.action_adapter import ActionAdapter, observation_action
from evaluation.real_aloha.register_demo import register

ROOT = Path(__file__).resolve().parents[1]


def load_file(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Registry = load_file(
    "registry_test", "wan_va/utils/Simple_Remote_Infer/deploy/registered_policy.py"
).RegisteredPolicy
history_module = load_file("history_test", "wan_va/utils/real_action_history.py")


def args_for(tmp_path, *extra):
    calibration = tmp_path / "calibration.json"
    calibration.write_text(
        json.dumps(dict(verified=True, policy_to_robot_rotation=np.eye(3).tolist()))
    )
    return parse_args(
        [
            "--calibration",
            str(calibration),
            "--output",
            str(tmp_path / "result"),
            "--prompt",
            "put block on scale",
            "--no-video",
            *extra,
        ]
    )


class ExecutableRobot(_FakeRobot):
    def reset(self, **kwargs):
        result = super().reset(**kwargs)
        result["metadata"].update(motion_enabled=True, frame="robot_base")
        return result


class HistoryPolicy(_FakePolicy):
    def get_server_metadata(self):
        return {"executed_action_history": True}

    def infer(self, request):
        result = super().infer(request)
        if "action" in result:
            result["action"][0, :, :] = 0.1
        return result


def test_actual_feedback_cached_and_partial_final_chunk_not_cached(tmp_path):
    args = args_for(
        tmp_path, "--mode", "execute", "--max-decisions", "2", "--max-actions", "5"
    )
    robot, policy = ExecutableRobot(), HistoryPolicy()
    summary = run_episode(args, robot=robot, policy=policy)
    assert summary["executed_actions"] == 5
    caches = [r for r in policy.calls if r.get("compute_kv_cache")]
    assert len(caches) == 1
    assert len(caches[0]["obs"]) == 4
    measured = caches[0]["executed_actions"]
    assert measured.shape == (16, 2, 4)
    assert np.all(measured[0] == 0)  # fake robot stayed still, policy requested +10cm
    assert np.allclose(measured[7, 1], 0.8)  # observed held gripper, not model zero


def test_shadow_resets_each_prediction_and_never_caches_fictitious_actions(tmp_path):
    args = args_for(tmp_path, "--max-decisions", "2", "--max-actions", "8")
    robot, policy = _FakeRobot(), HistoryPolicy()
    result = run_episode(args, robot=robot, policy=policy)
    assert result["executed_actions"] == robot.execute_calls == robot.preview_calls == 0
    assert len([r for r in policy.calls if r.get("reset")]) == 2
    assert not any(r.get("compute_kv_cache") for r in policy.calls)
    assert len(list(args.output.glob("observation_*.npz"))) == 9


def test_failure_record_survives_cleanup_failure(tmp_path):
    args = args_for(tmp_path)
    robot, policy = _FakeRobot(), _FakePolicy()

    def fail(_):
        raise RuntimeError("cleanup broken")

    robot.finish = fail
    policy.infer = lambda _: (_ for _ in ()).throw(ValueError("inference broken"))
    with pytest.raises(ValueError, match="inference broken"):
        run_episode(args, robot=robot, policy=policy)
    summary = json.loads((args.output / "summary.json").read_text())
    assert summary["error"] == "inference broken"
    assert summary["cleanup_errors"] == ["cleanup broken"]


def test_old_server_cannot_execute(tmp_path):
    args = args_for(tmp_path, "--mode", "execute")
    robot = ExecutableRobot()
    with pytest.raises(RuntimeError, match="executed_action_history"):
        run_episode(args, robot=robot, policy=_FakePolicy())
    assert robot.execute_calls == 0


def test_no_icl_guidance_defaults_to_text(tmp_path):
    assert args_for(tmp_path, "--no-icl").video_guidance_scale == 5
    with pytest.raises(SystemExit):
        args_for(tmp_path, "--no-icl", "--video-guidance-scale", "-1")


def test_measured_action_roundtrip_with_rotated_axes():
    from scipy.spatial.transform import Rotation

    q = Rotation.from_euler("xyz", [0.3, 0.2, -0.5]).as_quat()[[3, 0, 1, 2]]
    adapter = ActionAdapter(
        np.ones((2, 3)), np.stack([q, q]), Rotation.from_euler("z", 0.6).as_matrix()
    )
    action = np.array([0.02, -0.03, 0.01, 0, 0, 0, 1, 0.8] * 2)
    target = adapter.absolute_targets(action, [0.8, 0.8])
    obs = dict(
        eef_positions=[target[k]["position"] for k in ("left", "right")],
        eef_quaternions_wxyz=[target[k]["quaternion_wxyz"] for k in ("left", "right")],
        states=[0] * 6 + [0.8] + [0] * 6 + [0.8],
    )
    np.testing.assert_allclose(observation_action(adapter, obs), action, atol=1e-7)


def test_video_registration_and_legacy_registry(tmp_path):
    import imageio.v2 as imageio

    video = tmp_path / "demo.mp4"
    with imageio.get_writer(str(video), format="FFMPEG", fps=12) as writer:
        for _ in range(5):
            writer.append_data(np.zeros((32, 48, 3), np.uint8))
    registry = tmp_path / "registry.json"
    result = register(registry, "my_demo", video)
    assert len(result["sha256"]) == 64
    latent = tmp_path / "old.pth"
    latent.touch()
    data = json.loads(registry.read_text())
    data["legacy"] = "old.pth"
    registry.write_text(json.dumps(data))
    policy = _FakePolicy()
    wrapped = Registry(policy, registry)
    wrapped.infer(dict(reset=True, icl_id="my_demo", icl_latent_path="/stale"))
    assert policy.calls[-1]["icl_video_path"] == str(video)
    assert "icl_latent_path" not in policy.calls[-1]
    wrapped.infer(dict(reset=True, icl_id="legacy", icl_video_path="/stale"))
    assert policy.calls[-1]["icl_latent_path"] == str(latent)
    with pytest.raises(ValueError, match="already exists"):
        register(registry, "my_demo", video)


def test_history_rejects_partial_or_nan_before_cache_use():
    history_module.validate_history(np.zeros((16, 2, 16)), [{}] * 4, (16, 2, 16), True)
    with pytest.raises(ValueError, match="aligned"):
        history_module.validate_history(
            np.zeros((16, 2, 16)), [{}] * 3, (16, 2, 16), True
        )
    with pytest.raises(ValueError, match="values"):
        history_module.validate_history(
            np.full((16, 2, 16), np.nan), [{}] * 4, (16, 2, 16), True
        )


def test_readonly_bridge_rejects_commands_and_motion_constructor():
    with pytest.raises(ValueError, match="read-only"):
        PiperRosBackend(allow_motion=True)
    session = ReadOnlySession(SimpleNamespace(frame="robot_base"), {})
    reset = session.dispatch("reset", {})
    identity = {k: reset[k] for k in ("episode_id", "step_id")}
    with pytest.raises(ValueError, match="Read-only"):
        session.dispatch("chunk_step", dict(**identity, actions=[[0] * 14]))
    assert session.metadata["motion_enabled"] is False


def test_real_server_method_uses_override_without_loading_checkpoint(monkeypatch):
    # Compile the actual production method; avoid importing GPU/model dependencies.
    import torch

    tree = ast.parse((ROOT / "wan_va/wan_va_server.py").read_text())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VA_Server"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_compute_icl_kv_cache"
    )
    ns = dict(
        torch=torch,
        np=np,
        OBSERVATION_CACHE_TYPE=1,
        logger=SimpleNamespace(info=lambda *a: None),
        __package__="wan_va",
    )
    monkeypatch.setitem(sys.modules, "wan_va.utils.real_action_history", history_module)
    exec(
        compile(ast.Module(body=[method], type_ignores=[]), "production_cache", "exec"),
        ns,
    )
    calls = []

    class Transformer:
        def clear_prediction_cache(self, *a):
            pass

        def __call__(self, data, **kwargs):
            calls.append(data)

        def cache_counts(self):
            return {}

    server = SimpleNamespace(
        chunk_idx=0,
        cache_name="test",
        transformer=Transformer(),
        job_config=SimpleNamespace(
            used_action_channel_ids=list(range(16)),
            frame_chunk_size=2,
            action_per_frame=4,
        ),
        action_mask=torch.tensor([True] * 15 + [False]),
        init_latent=torch.zeros(1, 16, 1, 4, 1),
        last_predicted_actions=torch.ones(1, 16, 2, 4, 1) * 99,
        _encode_obs=lambda obs: torch.zeros(1, 16, 1, 4, 1),
        preprocess_action=lambda action: torch.from_numpy(action)[None, ..., None],
        _prepare_icl_stream=lambda tensor, *a: tensor.clone(),
    )
    ns["_compute_icl_kv_cache"](
        server,
        dict(obs=[{}] * 4, executed_actions=np.ones((16, 2, 4), np.float32) * 0.2),
    )
    assert torch.all(calls[1][:, :, 0] == 0)
    assert torch.allclose(calls[1][:, :15, 1], torch.full((1, 15, 4, 1), 0.2))
    assert torch.all(calls[1][:, 15] == 0)
    assert server.chunk_idx == 1
    # Existing simulation requests retain their original predicted cache path.
    server.chunk_idx = 0
    ns["_compute_icl_kv_cache"](server, dict(obs=[{}] * 4))
    assert torch.all(calls[-1] == 99)


def test_readonly_ik_preview_and_calibration_mismatch(tmp_path):
    from evaluation.real_aloha.robot_bridge.kinematics import ArmFK
    from evaluation.real_aloha.robot_bridge.geometry import pose

    urdf = tmp_path / "test_arm.urdf"
    urdf.write_text(
        '<robot name="test"><link name="base"/>'
        + "".join(
            '<link name="l%d"/><joint name="j%d" type="revolute">'
            '<parent link="%s"/><child link="l%d"/><origin xyz="0.1 0 0"/>'
            '<axis xyz="0 0 1"/></joint>'
            % (i, i, "base" if i == 0 else "l%d" % (i - 1), i)
            for i in range(6)
        )
        + "</robot>"
    )
    config = dict(
        urdf_path=str(urdf),
        joint_names=["j%d" % i for i in range(6)],
        arm_base="base",
        ee_link="l5",
        fk_roots=[np.eye(4).tolist()] * 2,
        joint_limits=[[-3, 3]] * 6,
        workspace=[[-2, 2]] * 3,
    )
    fk = ArmFK(str(urdf), config["joint_names"], base="base", tip="l5")
    measured = pose(fk.matrix(np.zeros(6)))
    backend = SimpleNamespace(
        frame="robot_base",
        get_state=lambda: (
            np.array([0] * 6 + [0.5] + [0] * 6 + [0.5]),
            np.array([measured["position"]] * 2),
            np.array([measured["quaternion_wxyz"]] * 2),
        ),
    )
    session = ReadOnlySession(backend, config)
    targets = {
        arm: dict(**measured, gripper_opening=0.5, gripper_closed=False)
        for arm in ("left", "right")
    }
    result = session.preview(targets)
    assert not result["executable"]
    assert result["diagnostics"]["left"]["residual_position_m"] < 1e-6
    session.roots[0, 0, 3] += 0.1
    with pytest.raises(ValueError, match="FK"):
        session.preview(targets)


def test_subscriber_backend_has_no_publish_calls():
    source = (ROOT / "evaluation/real_aloha/robot_bridge/piper_ros.py").read_text()
    tree = ast.parse(source)
    assert not any(
        isinstance(n, ast.Attribute) and n.attr in ("publish", "Publisher")
        for n in ast.walk(tree)
    )


def test_loopback_rpc_to_readonly_service(tmp_path):
    import threading
    from evaluation.real_aloha.astra_client import PiperControlClient
    from evaluation.real_aloha.robot_bridge.server import serve

    fake = _FakeRobot()
    backend = SimpleNamespace(
        frame="robot_base",
        get_state=lambda: (
            fake.obs["states"],
            fake.obs["eef_positions"],
            fake.obs["eef_quaternions_wxyz"],
        ),
        get_cameras=lambda: {
            k: fake.obs[k] for k in ("cam_high", "cam_left_wrist", "cam_right_wrist")
        },
    )
    session = ReadOnlySession(backend, {})
    ready = threading.Event()
    state = {}

    def on_ready(port):
        state["port"] = port
        ready.set()

    thread = threading.Thread(target=serve, args=(session, 0, on_ready), daemon=True)
    thread.start()
    assert ready.wait(5)
    with PiperControlClient(port=state["port"], timeout=5) as client:
        reset = client.reset()
        assert reset["metadata"]["motion_enabled"] is False
        np.testing.assert_array_equal(
            client.observation()["cam_high"], fake.obs["cam_high"]
        )
        client.finish("test")
    thread.join(5)
    assert not thread.is_alive()


def test_production_video_reader_handles_infinite_ffmpeg_frame_count(
    tmp_path, monkeypatch
):
    import imageio.v2 as imageio
    import torch
    import torch.nn.functional as F

    video = tmp_path / "unknown-frame-count.mp4"
    with imageio.get_writer(str(video), format="ffmpeg", fps=12) as writer:
        for _ in range(9):
            writer.append_data(np.zeros((32, 48, 3), np.uint8))
    monkeypatch.setitem(sys.modules, "decord", None)
    tree = ast.parse((ROOT / "wan_va/wan_va_server.py").read_text())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VA_Server"
    )
    methods = [
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef)
        and n.name in ("_read_icl_video", "_sample_icl_frame_indices")
    ]
    ns = dict(np=np, torch=torch, F=F)
    exec(
        compile(
            ast.Module(body=methods, type_ignores=[]), "production_video_reader", "exec"
        ),
        ns,
    )
    server = SimpleNamespace(
        job_config=SimpleNamespace(icl_height=32, icl_width=48, icl_fps=12)
    )
    server._sample_icl_frame_indices = lambda *args: ns["_sample_icl_frame_indices"](
        server, *args
    )
    result = ns["_read_icl_video"](server, str(video))
    assert result.shape == (1, 3, 9, 32, 48)
    assert torch.isfinite(result).all()


def test_raw_demo_uses_full_video_vae_and_preserves_last_frames(tmp_path):
    import os
    import torch

    video = tmp_path / "demo.mp4"
    video.touch()
    frames = torch.ones(1, 3, 6, 8, 8) * 255
    calls = []

    class VAE:
        config = SimpleNamespace(latents_mean=[0], latents_std=[1])

        def parameters(self):
            yield torch.zeros(1)

        def encode(self, video):
            calls.append(video.clone())
            return SimpleNamespace(
                latent_dist=SimpleNamespace(mode=lambda: torch.ones(1, 1, 3, 1, 1))
            )

    class Streaming:
        def __init__(self):
            self.clears = 0

        def clear_cache(self):
            self.clears += 1

        def encode_chunk(self, video):
            raise AssertionError(
                "Cold streaming encoder cannot accept the entire video"
            )

    tree = ast.parse((ROOT / "wan_va/wan_va_server.py").read_text())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VA_Server"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_load_or_encode_icl"
    )
    ns = dict(torch=torch, os=os, logger=SimpleNamespace(info=lambda *args: None))
    exec(
        compile(
            ast.Module(body=[method], type_ignores=[]),
            "production_video_encode",
            "exec",
        ),
        ns,
    )
    streaming = Streaming()
    server = SimpleNamespace(
        streaming_vae=streaming,
        vae=VAE(),
        dtype=torch.float32,
        _read_icl_video=lambda _: frames,
        normalize_latents=lambda mu, *args: mu,
    )
    latent, text = ns["_load_or_encode_icl"](server, str(video), "")
    assert calls[0].shape == (1, 3, 9, 8, 8)
    assert torch.all(calls[0] == 1)  # normalization and repeated tail
    assert latent.shape == (1, 1, 3, 1, 1)
    assert streaming.clears == 2 and text is None
