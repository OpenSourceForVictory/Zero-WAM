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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _load_calibration(path):
    data = json.loads(Path(path).read_text())
    rotation = np.asarray(data.get("policy_to_robot_rotation"), dtype=np.float64)
    if rotation.shape != (3, 3):
        raise ValueError("Calibration requires policy_to_robot_rotation (3x3)")
    if data.get("verified") is not True:
        raise ValueError("Refusing unverified installation calibration")
    return data, rotation


def run_episode(args, robot=None, policy=None):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    calibration, axis_map = _load_calibration(args.calibration)
    owns_robot = robot is None
    owns_policy = policy is None
    robot = robot or PiperControlClient(
        args.robot_host, args.robot_port, args.robot_timeout
    )
    policy = policy or WebsocketClientPolicy(
        host=args.policy_host,
        port=args.policy_port,
        connect_timeout=args.policy_connect_timeout,
        request_timeout=args.policy_timeout,
    )
    executed = 0
    decisions = 0
    finish_reason = "zero_wam_completed"
    try:
        reset = robot.reset(policy_version="zero-wam-real-v1", seed=args.seed)
        observation = robot.observation()
        metadata = reset.get("metadata", {})
        if args.mode == "execute" and not metadata.get("motion_enabled", False):
            raise RuntimeError("Robot control service reports motion_enabled=false")

        initial_positions = np.asarray(observation["eef_positions"], np.float64)
        initial_quaternions = np.asarray(
            observation["eef_quaternions_wxyz"], np.float64
        )
        adapter = ActionAdapter(
            initial_positions,
            initial_quaternions,
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
        policy.infer(reset_request)

        first = True
        # The released evaluator always feeds the episode's first observation to
        # the generation call. Later observations enter through the KV-cache
        # update below; replacing this value would change the policy contract.
        episode_initial_observation = _formatted_observation(observation, args.prompt)
        while decisions < args.max_decisions and executed < args.max_actions:
            response = policy.infer(
                dict(
                    obs=episode_initial_observation,
                    prompt=args.prompt,
                    save_visualization=False,
                    video_guidance_scale=args.video_guidance_scale,
                    action_guidance_scale=args.action_guidance_scale,
                )
            )
            action_chunk = np.asarray(response["action"], dtype=np.float32)
            if action_chunk.ndim != 3 or action_chunk.shape[0] != 16:
                raise ValueError(
                    f"Expected action shape (16, frames, steps), got {action_chunk.shape}"
                )
            if action_chunk.shape[2] % 4:
                raise ValueError("Action steps must be divisible by four")
            action_per_frame = action_chunk.shape[2] // 4
            key_frames = []
            start_frame = 1 if first else 0
            stop_episode = False
            for frame_index in range(start_frame, action_chunk.shape[1]):
                for step_index in range(action_chunk.shape[2]):
                    if executed >= args.max_actions:
                        stop_episode = True
                        break
                    model_action = action_chunk[:, frame_index, step_index]
                    current_openings = np.asarray(observation["states"])[[6, 13]]
                    requested = adapter.absolute_targets(model_action, current_openings)
                    targets, limit_diagnostics = limit_targets(
                        observation,
                        requested,
                        args.max_translation,
                        args.max_rotation,
                    )
                    record = dict(
                        decision=decisions,
                        action_index=executed,
                        frame_index=frame_index,
                        step_index=step_index,
                        mode=args.mode,
                        model_action=model_action.tolist(),
                        requested_targets=requested,
                        limited_targets=targets,
                        limit_diagnostics=limit_diagnostics,
                        timestamp=time.time(),
                    )
                    if args.mode in ("preview", "execute"):
                        proposal = robot.preview_eef(targets)
                        record["joint_proposal"] = proposal
                        if args.mode == "execute":
                            result = robot.execute(proposal)
                            record["execution"] = result
                    _write_json(output / f"action_{executed:04d}.json", record)
                    executed += 1

                    observation = robot.observation()
                    if (step_index + 1) % action_per_frame == 0:
                        key_frames.append(
                            _formatted_observation(observation, args.prompt)
                        )
                if stop_episode:
                    break

            if key_frames:
                policy.infer(
                    dict(
                        obs=key_frames,
                        compute_kv_cache=True,
                        imagine=False,
                        save_visualization=False,
                        state=action_chunk,
                    )
                )
            decisions += 1
            first = False
            if stop_episode:
                break
    except Exception as exc:
        finish_reason = f"zero_wam_error:{type(exc).__name__}"
        raise
    finally:
        summary = dict(
            mode=args.mode,
            decisions=decisions,
            actions=executed,
            finish_reason=finish_reason,
            calibration=str(args.calibration),
        )
        _write_json(output / "summary.json", summary)
        try:
            if getattr(robot, "identity", None) is not None:
                robot.finish(finish_reason)
        finally:
            if owns_robot:
                robot.close()
            if owns_policy:
                policy.close()
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
    parser.add_argument("--video-guidance-scale", type=float, default=-1.0)
    parser.add_argument("--action-guidance-scale", type=float, default=1.0)
    parser.add_argument("--icl-guidance-scale", type=float, default=5.0)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.max_decisions <= 0 or args.max_actions <= 0:
        parser.error("--max-decisions and --max-actions must be positive")
    if args.mode == "shadow" and args.follow_grippers:
        parser.error("--follow-grippers only applies to preview/execute")
    return args


def main():
    args = parse_args()
    summary = run_episode(args)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
