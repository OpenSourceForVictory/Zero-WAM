"""ROS1 subscriber-only service with bounded local IK previews. Never commands arms."""

import argparse
import json
from pathlib import Path
import socket
import uuid
import numpy as np
from scipy.spatial.transform import Rotation
from ..astra_client import _receive_packet, _send_packet, PROTOCOL_VERSION
from .kinematics import ArmFK
from .geometry import transform
from .contract import validate_joint_limits, validate_workspace, validate_bounded


class ReadOnlySession:
    def __init__(self, backend, config):
        self.backend, self.config = backend, config
        self.identity = None
        self.finished = False
        self.fk = None
        if config.get("urdf_path"):
            self.fk = ArmFK(
                config["urdf_path"],
                config["joint_names"],
                base=config["arm_base"],
                tip=config["ee_link"],
            )
            self.roots = np.asarray(config["fk_roots"], float)
            # Reuse rigid-transform validation without importing ROS.
            from .piper_ros import PiperRosBackend

            PiperRosBackend(base_transforms=self.roots)
            validate_joint_limits(
                np.mean(config["joint_limits"], axis=1), config["joint_limits"]
            )
            validate_workspace(
                np.mean(config["workspace"], axis=1), config["workspace"]
            )
        self.metadata = dict(
            motion_enabled=False,
            readonly_preview=self.fk is not None,
            frame=getattr(backend, "frame", "unknown"),
            physical_hardware=True,
            robot_adapter="aloha_piper_readonly",
            max_episode_steps=0,
        )

    def observe(self):
        state, positions, quaternions = self.backend.get_state()
        return dict(
            **self.backend.get_cameras(),
            states=state,
            eef_positions=positions,
            eef_quaternions_wxyz=quaternions,
        )

    def preview(self, targets):
        if self.fk is None or self.metadata["frame"] != "robot_base":
            raise ValueError(
                "Preview requires calibrated URDF, FK roots and common base frame"
            )
        if set(targets) != {"left", "right"}:
            raise ValueError("Explicit left/right targets required")
        state, positions, quaternions = self.backend.get_state()
        actions, diagnostics = [], {}
        for i, arm in enumerate(("left", "right")):
            q = np.asarray(state[i * 7 : i * 7 + 6])
            validate_joint_limits(q, self.config["joint_limits"])
            predicted = self.roots[i] @ self.fk.matrix(q)
            measured = transform(positions[i], quaternions[i])
            if (
                np.linalg.norm(predicted[:3, 3] - measured[:3, 3]) > 0.005
                or Rotation.from_matrix(
                    predicted[:3, :3] @ measured[:3, :3].T
                ).magnitude()
                > 0.03
            ):
                raise ValueError("Calibrated FK does not match measured TCP")
            validate_bounded(
                dict(position=positions[i], quaternion_wxyz=quaternions[i]),
                targets[arm],
            )
            validate_workspace(positions[i], self.config["workspace"])
            validate_workspace(targets[arm]["position"], self.config["workspace"])
            joints, diag = self.fk.bounded_target(
                q, self.config["joint_limits"], self.roots[i], targets[arm]
            )
            candidate = self.roots[i] @ self.fk.matrix(joints)
            validate_joint_limits(joints, self.config["joint_limits"])
            validate_workspace(candidate[:3, 3], self.config["workspace"])
            opening = targets[arm]["gripper_opening"]
            if not np.isfinite(opening) or not 0 <= opening <= 1:
                raise ValueError("Invalid gripper opening")
            actions.extend([*joints.tolist(), float(opening)])
            diag["residual_position_m"] = float(
                np.linalg.norm(candidate[:3, 3] - targets[arm]["position"])
            )
            diag["collision_checked"] = False
            diagnostics[arm] = diag
        return dict(
            action=actions, diagnostics=diagnostics, executable=False, physical_steps=0
        )

    def dispatch(self, op, args):
        if op == "metadata":
            return self.metadata
        if op == "reset":
            if self.identity is not None:
                raise ValueError("One robot session per connection")
            self.identity = dict(episode_id=uuid.uuid4().hex, step_id=0)
            return dict(**self.identity, metadata=self.metadata)
        if (
            self.finished
            or self.identity is None
            or any(args.get(k) != v for k, v in self.identity.items())
        ):
            raise ValueError("Stale or finished robot session")
        if op == "teacher_observation":
            return self.observe()
        if op == "eef_joint_target":
            return self.preview(args["targets"])
        if op == "finish_pilot":
            self.finished = True
            return dict(physical_steps=0)
        raise ValueError("Read-only service: unsupported operation " + op)


def serve(session, port, on_ready=None):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        print(
            json.dumps(dict(port=listener.getsockname()[1], metadata=session.metadata)),
            flush=True,
        )
        if on_ready is not None:
            on_ready(listener.getsockname()[1])
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(900)
            while not session.finished:
                try:
                    request = _receive_packet(connection)
                except (EOFError, ConnectionError, TimeoutError):
                    break
                response = dict(
                    version=PROTOCOL_VERSION, request_id=request.get("request_id")
                )
                try:
                    if request.get("version") != PROTOCOL_VERSION:
                        raise ValueError("Protocol version mismatch")
                    result = session.dispatch(request["op"], request.get("args", {}))
                    response.update(ok=True, result=result)
                except Exception as exc:
                    response.update(ok=False, error=str(exc))
                _send_packet(connection, response)
                if not response["ok"]:
                    break


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--port", type=int, default=19140)
    a = p.parse_args()
    config = json.loads(a.config.read_text())
    if config.get("verified") is not True or not config.get("base_transforms"):
        p.error("Verified common base transforms required")
    if config.get("urdf_path"):
        config["urdf_path"] = str(
            (a.config.resolve().parent / config["urdf_path"]).resolve()
        )
    from .piper_ros import PiperRosBackend

    backend = PiperRosBackend(
        base_transforms=config["base_transforms"], gripper_max_m=config["gripper_max_m"]
    )
    session = ReadOnlySession(backend, config)
    try:
        backend.connect()
        backend.wait_ready()
        serve(session, a.port)
    finally:
        backend.close()


if __name__ == "__main__":
    main()
