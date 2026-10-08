# Zero-WAM on split ALOHA / dual Piper

This directory supports the next deployment milestone: real camera observations
and a human video prompt, saved predictions, and **read-only local IK preview**.
Hardware operation and task success have not been validated. The currently used
RoboTwin checkpoint is not evidence of reliable transfer to Piper.

The ROS service is now included in `robot_bridge/`; `/tmp/astra-manip-src` and the
GPT-as-Policy checkout are no longer runtime dependencies. Reference license and
source hashes are retained in that directory. It creates subscribers only and
rejects all execution operations. Local DLS previews are approximate kinematic
steps, not collision-checked trajectories or proof of reachability.

## Dependencies

Run from the Zero-WAM repository root. On the client, Python 3.10+:

```bash
python -m pip install -r evaluation/real_aloha/requirements-client.txt
```

On the ROS Noetic machine, use its existing ROS Python environment with numpy,
scipy and msgpack, plus `rospy`, `cv_bridge`, `sensor_msgs`, `geometry_msgs`, and
the installed driver's `piper_msgs`. Do not install model/PyTorch dependencies
into the ROS environment. `robot_bridge` and its RPC codec work on Python 3.8.

## Register your human demonstration on the inference computer

Record a fixed-view MP4 that clearly shows the objects, human manipulation and
completed task. Trim unrelated waiting. This is a practical first-test suggestion,
not a checkpoint guarantee. Transfer it to the **inference host** using your
normal file-transfer method (for example `scp`). The RPC client does not upload
files. Then, on that host:

```bash
python -m evaluation.real_aloha.register_demo \
  --registry deployment/ascend/icl-registry.json \
  --id my_scale_demo_v1 --video /data/demos/my_scale_demo.mp4

./run_inference.sh --serve --host 127.0.0.1 --port 29056 \
  --steps 50 --icl-registry deployment/ascend/icl-registry.json
```

The registration command validates video readability and prints its SHA256.
It refuses to overwrite an existing ID. Register while the service is stopped;
restart it to pick up registry changes. Video entries use this schema:

```json
{
  "place_object_scale": "../../data/place_object_scale_icl.pth",
  "my_scale_demo_v1": {"video": "/data/demos/my_scale_demo.mp4"}
}
```

Legacy latent paths still work. Raw videos are sampled at the configured 12 fps,
resized to 320x480 (height x width), VAE-encoded and cached at reset. The encoded
result is not automatically saved to disk. Long videos consume more memory;
video encoding on the deployed Ascend model still needs a real-service test.
Keep the registered file immutable so the ID remains reproducible.

## Start the read-only robot bridge

Create a measured configuration outside version control. Required observation
fields are `verified: true`, `base_transforms` (two measured 4x4
robot-base-from-arm-base transforms), and `gripper_max_m` (measured opening).
No made-up baseline or TCP values are supplied. For IK preview also provide:

- `urdf_path`: matching Piper variant, relative to the config or absolute;
- `joint_names`: six URDF joint names in feedback order;
- `arm_base`, `ee_link`: calibrated base and TCP link in the URDF;
- `fk_roots`: two measured robot-base-from-URDF-base transforms;
- `joint_limits`: six `[min, max]` pairs in radians;
- `workspace`: three `[min, max]` pairs in meters, in the common robot base.

`ee_link` must represent the same TCP as the driver's measured end pose. Merely
changing a config flag is not calibration. The bridge checks FK against feedback
before each preview. Mechanical zero, base transforms and TCP must be verified
on the installed hardware.

```bash
python -m evaluation.real_aloha.robot_bridge.server \
  --config /path/to/measured-piper.json --port 19140
```

Topics follow the installed Modular ALOHA ROS1 driver:
`/puppet/joint_left`, `/puppet/joint_right`, `/puppet/end_pose_left`,
`/puppet/end_pose_right`, `/puppet/arm_status_left`, `/puppet/arm_status_right`;
cameras `/camera_f/color/image_raw`, `/camera_l/color/image_raw`,
`/camera_r/color/image_raw` (640x480 RGB). The official single-arm driver has a
different topic contract. Faults or stale feedback are rejected.

The service is single-client, loopback-only and exits after one client session.
Forward it over SSH when needed. Restart for a new experiment. Use the existing
SSH inference tunnel at client port 39056, or supply another policy host/port.

## Shadow experiment and preview

Copy `calibration.example.json` to `calibration.local.json`, set the measured
`policy_to_robot_rotation`, and mark verified only after validation. This matrix
maps translation axes; orientation deltas assume matching policy/robot TCP axis
conventions. It does not fix a differing tool frame automatically.

```bash
python -m evaluation.real_aloha.run_policy \
  --mode shadow --robot-port 19140 --policy-port 39056 \
  --prompt "Use one arm to grab the object and put it on the scale" \
  --icl-id my_scale_demo_v1 \
  --calibration evaluation/real_aloha/calibration.local.json \
  --output results/real-aloha/shadow-001 \
  --max-decisions 1 --max-actions 16
```

Use `--mode preview` with the same arguments to obtain bounded IK from the
read-only bridge. Grippers remain at measured openings by default. Each shadow
or preview decision is a fresh policy episode; stationary observations are not
fed back as though predicted motions were executed.

Outputs include:

- `manifest.json`: arguments, calibration and server metadata;
- `prediction_*.npy`: complete raw policy chunks;
- `action_*.json`: requested/limited poses, preview diagnostics, measured feedback,
  inference latency and command status;
- `observation_*.npz`: lossless three-camera frames, state, EEF and wall timestamp;
- `observations.mp4`: head/left/right montage at **5 fps for inspection**, not real
  control timing; `--no-video` disables MP4 while retaining NPZ snapshots;
- `summary.json`: prediction and acknowledged execution counts, errors and cleanup
  errors. `success: null` means task success has not been evaluated.

For a text-only comparison use `--no-icl`. This now defaults to text guidance 5;
ICL defaults remain video/text guidance -1 and ICL guidance 5.

## Execution boundary and history protocol

### Existing LingBot Piper end-pose control

The hardware host's `lingbot-va-piper/client/real_robot_inference.py` implements
16D relative EEF composition followed by
`PiperController.send_end_pose_with_gripper(left_xyz_rpy, right_xyz_rpy, left_m,
right_m)`. Its controller uses `move_p`, accepting absolute flange poses in each
arm's own base (meters and XYZ extrinsic Euler radians). This differs from the
`real_robot_inference_merged.py` MOVE_JS joint-action path.

`piper_eef.PiperEEFAdapter` prepares this end-pose method's arguments directly,
without an external IK solver or any SDK import. Supply episode-start flange
poses, explicit task-from-base and flange-from-TCP transforms for both arms,
the policy translation-axis rotation, and measured gripper maximum openings.
Identity base/tool transforms represent the old `--no_transform --tcp_offset 0`
convention only when that convention is actually appropriate for the installation.
Do not infer it merely because a calibration file is missing.

`prepare(action, current_flange_poses, current_grippers_m)` reconstructs the
episode-relative EEF target, limits it from the current TCP feedback to 4 mm /
0.025 radians, converts to per-arm flange xyz/rpy, and holds measured gripper
openings by default. `follow_grippers=True` converts normalized model openings
to meters. Returned `controller_arguments` match the existing end-pose method.
The preview explicitly reports `executable=False`: it does not connect, enable,
send, check collisions/reachability, or validate feedback freshness. It is not
yet wired into the RPC execution loop. The existing ROS bridge remains read-only.

Before sending these arguments, the motion service must enforce fresh feedback,
confirmed installation conventions, workspace and stopping checks, and report
actual measured EEF history. Supplying a validated format is not evidence that
an autonomous grasp is commissioned. This path does not require replacing the
existing working end-pose controller with a joint-space implementation.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_piper_eef.py
```

The bundled robot bridge cannot execute, enable, home, zero or disable hardware.
The client's existing `execute` interface is for a separately commissioned motion
service. No complete real task or autonomous grasp is claimed here. It also
requires inference metadata `executed_action_history: true` (restart the updated
Ascend server), verified common coordinates and fresh feedback after inference.
A prediction older than `--max-prediction-age` (default 60 seconds), or a robot
state changed during inference, is rejected before sending targets.

When continuing a complete execution chunk, the client maps **measured EEF and
gripper feedback** back into the model's episode-relative action convention and
sends `executed_actions`. The server validates observation/action alignment and
normalizes this history. Legacy simulation calls retain the original predicted
history path. A terminal partial chunk is not cached or padded. This corrects
the old behavior where limited motion was followed by caching the unlimited
prediction; it does not establish real-time tracking performance.

The site still needs verification of control timing, driver speed, stopping,
collision constraints, gripper contact and task success labeling before moving
from single-step commissioning to continuous task trials.

## Offline checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tests/test_real_aloha_action_adapter.py tests/test_real_aloha_deployment.py
```

The tests use synthetic observations and fake policies, exercise the production
cache-update method without model weights, and do not connect to hardware.
Official references: [Piper SDK](https://github.com/agilexrobotics/piper_sdk),
[dual-arm mode guide](https://github.com/agilexrobotics/piper_sdk/blob/master/asserts/double_piper.MD),
[variant-specific URDFs](https://github.com/agilexrobotics/agx_arm_urdf).

## Preflight against a running inference service

Before connecting cameras, the model can be exercised with recorded simulation
images and a registered test MP4. This never opens a robot connection:

```bash
python -m evaluation.real_aloha.probe_policy \
  --input /path/to/recorded-observations \
  --video-id codec_probe_synthetic \
  --output results/real-aloha/model-preflight
```

The input directory contains the three `observation.images.cam_*.mp4` files.
The probe checks MP4 reset/encoding and prediction, existing latent-based
prediction, explicit stationary history and a subsequent prediction. Its repeated
images and synthetic stationary action history are a protocol test, not real
robot feedback or a task-success evaluation. Reports are saved even on failure.

## Migrated LingBot merged client: direct EEF execution

`run_piper_eef.py` is a copy of the site's merged client with a separate 16D
Zero-WAM EEF branch. Per the site's deployment choice, it retains the existing
connect/enable/camera/control lifecycle; **it is an execution entry, not a
preview**. The original hardware-host `real_robot_inference_merged.py` is unchanged.
Hardware imports come from `LINGBOT_CLIENT_DIR` (default
`/home/agilex/models/lingbot-va-piper/client`), using its `piper_controller.py`,
`camera_manager.py`, `va_client.py`, and existing utilities. This is not yet a
standalone replacement for those installed hardware dependencies.

The new branch accepts `(16, 2, 16)`, skips the initial conditioning frame on the
first chunk, reconstructs EEF actions relative to episode-start poses, converts
quaternions to xyz Euler radians, and invokes `send_end_pose_with_gripper` /
MOVE_P. Normalized model gripper openings are converted to meters using the
existing controller's 0.08 m range. Joint smoothing is bypassed. The original
14/28D joint branches remain. `--control_mode movejs` preserves the familiar
startup/speed defaults, but **16D commands themselves are MOVE_P, not MOVE_JS**.

ICL reset is sent explicitly in this file, so it does not depend on the previous
`va_client.reset` modification. Before another chunk, real measured EEF/gripper
history is sent as `executed_actions`. A runtime stop is checked before every
EEF action. Do not run this entry concurrently with another hardware controller.

On the robot host, after deploying the project package:

```bash
source /opt/ros/noetic/setup.bash
export LD_PRELOAD=/lib/x86_64-linux-gnu/libffi.so.7
cd /home/agilex/zero-wam-readonly
/home/agilex/miniconda3/envs/piper_new/bin/python \
  -m evaluation.real_aloha.run_piper_eef \
  --server-host 127.0.0.1 --server-port 39056 \
  --icl-id bread_plate_20261008_v1 \
  --prompt "Take two plates from the rack and place them on the tray. Put one slice of bread on each plate." \
  --control_mode movejs --max_chunks 1
```

This command enables the robot and can move it. The directory name
`zero-wam-readonly` is historical; it does not make this new entry read-only.
Frames/TCP follow the legacy EEF script: `--tcp_offset` defaults to zero, and
right-base calibration is attempted unless `--no_transform` is supplied. A missing
calibration file falls back to each arm's own base, with a printed message, just
as the old script did. That fallback is not a calibration validation. The
existing lifecycle/speed/gripper settings are retained, not newly commissioned
for this policy. Hardware movement and task success have not been tested for
this entry. Unit tests use simulated controllers; the on-device check only loads
`--help`.

Predictions, measured EEF arrays and key-camera images are saved to the timestamped
`inference_logs` run directory. The old `logger.finish` remains disabled as in the
site source; do not interpret a missing exception as a successful physical task.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_piper_merged_eef.py
```
