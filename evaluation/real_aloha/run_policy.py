"""Run Zero-WAM against a split dual-Piper ALOHA observation/control service."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from evaluation.real_aloha.action_adapter import ActionAdapter, limit_targets
from evaluation.real_aloha.astra_client import PiperControlClient
from evaluation.robotwin.websocket_client_policy import WebsocketClientPolicy


CAMERA_MAP = {
    "cam_high": "observation.images.cam_high",
    "cam_left_wrist": "observation.images.cam_left_wrist",
    "cam_right_wrist": "observation.images.cam_right_wrist",
}


def _formatted_observation(observation, prompt):
    result = {target: observation[source] for source, target in CAMERA_MAP.items()}
    result["observation.state"] = np.asarray(observation["states"], np.float32)
    result["task"] = prompt
    return result


def _load_calibration(path):
    data = json.loads(Path(path).read_text())
    rotation = np.asarray(data.get("policy_to_robot_rotation"), dtype=np.float64)
    if rotation.shape != (3, 3):
        raise ValueError("Calibration requires policy_to_robot_rotation (3x3)")
    if data.get("verified") is not True:
        raise ValueError("Refusing unverified installation calibration")
    return data, rotation


def run_episode(args, robot=None, policy=None):
    from .action_adapter import observation_action
    from .recording import Recorder, write_json

    calibration, axis_map = _load_calibration(args.calibration)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    recorder = Recorder(output, video=not getattr(args, "no_video", False))
    owns_robot, owns_policy = robot is None, policy is None
    count = executed = decisions = 0
    finish_reason = "zero_wam_completed"
    error = None
    cleanup_errors = []
    metadata = {}
    try:
        # Model connection failure must still close an already-created robot socket.
        robot = robot or PiperControlClient(
            args.robot_host, args.robot_port, args.robot_timeout
        )
        policy = policy or WebsocketClientPolicy(
            host=args.policy_host,
            port=args.policy_port,
            connect_timeout=args.policy_connect_timeout,
            request_timeout=args.policy_timeout,
        )
        metadata = (
            policy.get_server_metadata()
            if hasattr(policy, "get_server_metadata")
            else {}
        )
        reset = robot.reset(policy_version="zero-wam-real-v2", seed=args.seed)
        robot_metadata = reset.get("metadata", {})
        if args.mode == "execute" and not robot_metadata.get("motion_enabled", False):
            raise RuntimeError("Robot control service reports motion_enabled=false")
        if args.mode == "execute" and not metadata.get(
            "executed_action_history", False
        ):
            raise RuntimeError(
                "Inference server lacks executed_action_history capability; update it first"
            )
        if args.mode == "preview" and not robot_metadata.get("readonly_preview", False):
            raise RuntimeError("Preview requires a read-only IK service")
        if (
            args.mode in ("preview", "execute")
            and robot_metadata.get("frame") != "robot_base"
        ):
            raise RuntimeError("Calibrated common robot_base frame required")
        write_json(
            output / "manifest.json",
            dict(
                arguments=vars(args)
                | {"output": str(args.output), "calibration": str(args.calibration)},
                calibration=calibration,
                robot=robot_metadata,
                policy=metadata,
                video_note="Inspection montage at 5 fps; NPZ timestamps are wall time, not control rate",
            ),
        )
        observation = robot.observation()
        recorder.observe(observation)
        adapter = ActionAdapter(
            observation["eef_positions"],
            observation["eef_quaternions_wxyz"],
            axis_map,
            hold_grippers=not args.follow_grippers,
            gripper_threshold=float(calibration.get("gripper_threshold", 0.5)),
        )
        reset_request = dict(
            reset=True,
            prompt=args.prompt,
            save_visualization=False,
            use_icl=not args.no_icl,
            video_guidance_scale=args.video_guidance_scale,
            icl_guidance_scale=args.icl_guidance_scale,
        )
        if not args.no_icl:
            reset_request["icl_id"] = args.icl_id
        first_observation = _formatted_observation(observation, args.prompt)
        policy.infer(reset_request)
        first = True
        while decisions < args.max_decisions and count < args.max_actions:
            # Shadow/preview are independent predictions of a stationary scene,
            # not synthetic executed trajectories inserted into the model cache.
            if decisions and args.mode != "execute":
                policy.infer(reset_request)
                first = True
                first_observation = _formatted_observation(observation, args.prompt)
                adapter = ActionAdapter(
                    observation["eef_positions"],
                    observation["eef_quaternions_wxyz"],
                    axis_map,
                    hold_grippers=not args.follow_grippers,
                    gripper_threshold=float(calibration.get("gripper_threshold", 0.5)),
                )
            before = np.asarray(observation["states"]).copy()
            started = time.monotonic()
            response = policy.infer(
                dict(
                    obs=first_observation,
                    prompt=args.prompt,
                    save_visualization=False,
                    video_guidance_scale=args.video_guidance_scale,
                    action_guidance_scale=args.action_guidance_scale,
                )
            )
            inference_seconds = time.monotonic() - started
            actions = np.asarray(response["action"], np.float32)
            if (
                actions.ndim != 3
                or actions.shape[0] != 16
                or actions.shape[1] != 2
                or actions.shape[2] <= 0
                or actions.shape[2] % 4
                or not np.isfinite(actions).all()
            ):
                raise ValueError(
                    "Expected a finite (16, 2, positive multiple of 4) action chunk"
                )
            np.save(output / ("prediction_%04d.npy" % decisions), actions)
            history = np.zeros_like(actions)
            observation = robot.observation()  # fresh feedback after slow inference
            if args.mode == "execute":
                if inference_seconds > getattr(args, "max_prediction_age", 60):
                    raise RuntimeError("Prediction too old to execute")
                if np.max(np.abs(np.asarray(observation["states"]) - before)) > 0.002:
                    raise RuntimeError(
                        "Robot moved during inference; prediction discarded"
                    )
            key_frames = []
            complete = True
            for f in range(int(first), actions.shape[1]):
                for j in range(actions.shape[2]):
                    if count >= args.max_actions:
                        complete = False
                        break
                    if args.mode == "execute" and time.monotonic() - started > getattr(
                        args, "max_prediction_age", 60
                    ):
                        raise RuntimeError("Prediction expired during chunk execution")
                    observation = robot.observation()
                    requested = adapter.absolute_targets(
                        actions[:, f, j], np.asarray(observation["states"])[[6, 13]]
                    )
                    targets, diagnostics = limit_targets(
                        observation, requested, args.max_translation, args.max_rotation
                    )
                    record = dict(
                        decision=decisions,
                        action_index=count,
                        mode=args.mode,
                        model_action=actions[:, f, j],
                        requested_targets=requested,
                        limited_targets=targets,
                        limit_diagnostics=diagnostics,
                        inference_seconds=inference_seconds,
                        status="prepared",
                        timestamp=time.time(),
                    )
                    path = output / ("action_%04d.json" % count)
                    write_json(
                        path, record
                    )  # preserve intent even if RPC execution is uncertain
                    if args.mode in ("preview", "execute"):
                        proposal = robot.preview_eef(targets)
                        record["joint_proposal"] = proposal
                        if args.mode == "execute":
                            record["execution"] = robot.execute(proposal)
                            executed += 1
                    observation = robot.observation()
                    history[:, f, j] = observation_action(adapter, observation)
                    record.update(
                        status="acknowledged" if args.mode == "execute" else "observed",
                        measured_action=history[:, f, j],
                        observation=recorder.observe(observation),
                    )
                    write_json(path, record)
                    count += 1
                    if (j + 1) % (actions.shape[2] // 4) == 0:
                        key_frames.append(
                            _formatted_observation(observation, args.prompt)
                        )
                if not complete:
                    break
            decisions += 1
            continuing = decisions < args.max_decisions and count < args.max_actions
            # Never pad incomplete feedback or pretend unexecuted actions happened.
            if args.mode == "execute" and continuing:
                if not complete:
                    raise RuntimeError("Cannot continue from a partial action chunk")
                policy.infer(
                    dict(
                        obs=key_frames,
                        compute_kv_cache=True,
                        executed_actions=history,
                        imagine=False,
                        save_visualization=False,
                    )
                )
            first = False
    except BaseException as exc:
        finish_reason = "zero_wam_error:" + type(exc).__name__
        error = str(exc)
        raise
    finally:
        # Cleanup must not mask the original execution error or prevent other cleanup.
        for operation in (
            lambda: (
                robot.finish(finish_reason)
                if robot and getattr(robot, "identity", None)
                else None
            ),
            lambda: robot.close() if robot and owns_robot else None,
            lambda: policy.close() if policy and owns_policy else None,
            recorder.close,
        ):
            try:
                operation()
            except BaseException as exc:
                cleanup_errors.append(str(exc))
        summary = dict(
            mode=args.mode,
            decisions=decisions,
            actions=count,
            executed_actions=executed,
            finish_reason=finish_reason,
            error=error,
            cleanup_errors=cleanup_errors,
            observation_count=recorder.count,
            success=None,
            calibration=str(args.calibration),
        )
        write_json(output / "summary.json", summary)
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("shadow", "preview", "execute"), default="shadow"
    )
    parser.add_argument("--robot-host", default="127.0.0.1")
    parser.add_argument("--robot-port", type=int, default=19140)
    parser.add_argument("--robot-timeout", type=float, default=300.0)
    parser.add_argument("--policy-host", default="127.0.0.1")
    parser.add_argument("--policy-port", type=int, default=39056)
    parser.add_argument("--policy-connect-timeout", type=float, default=60.0)
    parser.add_argument("--policy-timeout", type=float, default=300.0)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--icl-id", default="place_object_scale")
    parser.add_argument("--no-icl", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-decisions", type=int, default=1)
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--max-translation", type=float, default=0.004)
    parser.add_argument("--max-rotation", type=float, default=0.025)
    parser.add_argument("--follow-grippers", action="store_true")
    parser.add_argument("--video-guidance-scale", type=float, default=None)
    parser.add_argument("--no-video", action="store_true")
    parser.add_argument("--max-prediction-age", type=float, default=60.0)
    parser.add_argument("--action-guidance-scale", type=float, default=1.0)
    parser.add_argument("--icl-guidance-scale", type=float, default=5.0)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.max_decisions <= 0 or args.max_actions <= 0:
        parser.error("--max-decisions and --max-actions must be positive")
    if args.mode == "shadow" and args.follow_grippers:
        parser.error("--follow-grippers only applies to preview/execute")
    if args.video_guidance_scale is None:
        args.video_guidance_scale = 5.0 if args.no_icl else -1.0
    if args.no_icl and args.video_guidance_scale <= 1:
        parser.error("Text-only comparison requires --video-guidance-scale > 1")
    if (
        not np.isfinite(
            [args.max_translation, args.max_rotation, args.max_prediction_age]
        ).all()
        or min(args.max_translation, args.max_rotation, args.max_prediction_age) <= 0
    ):
        parser.error("Limits must be finite and positive")
    if args.mode == "execute" and (
        args.max_translation > 0.005 or args.max_rotation > 0.03
    ):
        parser.error("Commissioning limits: 5 mm / 0.03 rad")
    return args


def main():
    args = parse_args()
    summary = run_episode(args)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
