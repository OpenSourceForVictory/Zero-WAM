"""Check the deployed model using recorded images; never connects to a robot.

The action-history check uses synthetic stationary feedback and repeated images.
It verifies protocol/model compatibility, not task success or physical tracking.
"""

import argparse
import json
from pathlib import Path
import time

import imageio.v2 as imageio
import numpy as np

from evaluation.robotwin.websocket_client_policy import WebsocketClientPolicy

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


def probe(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    observation = {}
    for camera in CAMERAS:
        key = "observation.images." + camera
        with imageio.get_reader(
            str(args.input / (key + ".mp4")), format="ffmpeg"
        ) as reader:
            observation[key] = reader.get_data(0).copy()
    report = dict(
        physical_commands=0,
        input_source="recorded_simulation_images",
        action_history_source="synthetic_stationary_feedback",
        checks=[],
    )
    client = None

    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    def request(label, data):
        started = time.monotonic()
        result = client.infer(data)
        row = dict(
            check=label,
            seconds=time.monotonic() - started,
            server_timing=result.get("server_timing"),
        )
        if "action" in result:
            action = np.asarray(result["action"])
            if action.shape != (16, 2, 16) or not np.isfinite(action).all():
                raise ValueError("Invalid model action: " + str(action.shape))
            np.save(output / (label + ".npy"), action)
            row.update(shape=list(action.shape), finite=True)
        report["checks"].append(row)
        save()
        print(json.dumps(row), flush=True)
        return result

    try:
        client = WebsocketClientPolicy(args.host, args.port, request_timeout=300)
        report["metadata"] = client.get_server_metadata()
        if not report["metadata"].get("executed_action_history"):
            raise RuntimeError("Server lacks measured-history support")
        for demo in (args.video_id, args.latent_id):
            if demo not in report["metadata"].get("icl_ids", []):
                raise ValueError("Demo ID not registered: " + demo)
        for kind, demo in (("video", args.video_id), ("latent", args.latent_id)):
            request(
                kind + "_reset",
                dict(
                    reset=True,
                    use_icl=True,
                    icl_id=demo,
                    prompt=args.prompt,
                    video_guidance_scale=-1,
                    icl_guidance_scale=5,
                ),
            )
            request(kind + "_prediction", dict(obs=observation, prompt=args.prompt))
            if kind == "latent":
                history = np.zeros((16, 2, 16), np.float32)
                history[[6, 14], 1, :] = 1  # identity xyzw deltas
                history[[7, 15], 1, :] = 0.5
                request(
                    "stationary_history",
                    dict(
                        obs=[observation] * 4,
                        compute_kv_cache=True,
                        executed_actions=history,
                    ),
                )
                request(
                    "prediction_after_history",
                    dict(obs=observation, prompt=args.prompt),
                )
        report["passed"] = True
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        save()
        if client:
            client.close()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=39056)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--video-id", required=True)
    p.add_argument("--latent-id", default="place_object_scale")
    p.add_argument(
        "--prompt", default="Use one arm to grab the object and put it on the scale."
    )
    args = p.parse_args()
    probe(args)


if __name__ == "__main__":
    main()
