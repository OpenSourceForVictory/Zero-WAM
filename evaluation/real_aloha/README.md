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
quaternions to xyz Euler radians, and solves local IK before sending MOVE_J
joint targets. Normalized model gripper openings are converted to meters using the
existing controller's 0.08 m range. Joint smoothing is bypassed. The original
14/28D joint branches remain. `--control_mode movejs` preserves the familiar
startup/speed defaults, but **16D commands themselves are checked-IK MOVE_J, not MOVE_JS**.

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


### EEF controller rejection and arrival checks

`piper_kinematic_guard.py` reads actual limits from BOTH controllers and uses the
installed SDK's Piper MDH. Before enabling, it rejects starts within 0.01 rad of
a joint boundary or with normalized Jacobian minimum singular value below 0.015.
Before sending either arm, it checks FK against flange feedback, solves nearby
IK along 1 mm / 0.01 rad Cartesian samples, limits total joint change to 0.05 rad,
and checks the joint-linear path for boundaries and singularities. Local plans
larger than 20 mm / 0.1 rad are rejected. These are commissioning thresholds, not
manufacturer-certified operating limits.

The selected joint endpoint is sent with MOVE_J, so MOVE_P cannot select a
different IK branch. Feedback must remain in the checked joint interval (0.02 rad
tolerance), and both joint and flange arrival must be confirmed. The raw model
prediction, IK plan, commanded joints and measured result remain inspectable.
Scene collision checking and verified velocity/acceleration/stopping behavior
are still absent; the gate must not be treated as a commissioned motion planner.
No physical test of the new joint execution path has been performed.

#### Offline action-contract audit (2026-10-08)

Replaying the recorded 20:42:55 prediction reproduces both logged limited flange
targets to numerical precision. Its first executed-frame sample requests roughly
279 mm left / 220 mm right relative to episode start, before limiting. These are
requested offsets, not measured travel. The scalar-first/scalar-last boundary
still needs resolution: the inspected RoboTwin `Robot._trans_endpose()` returns
world positions and `transforms3d.mat2quat()` (wxyz), while Zero-WAM's simulation
`add_eef_pose()` feeds these arrays directly to SciPy's default xyzw operations.
The PiPER entry instead constructs a physical xyzw quaternion from flange Euler
angles. Matching the numeric addition/multiplication formulas does not establish
matching physical semantics. With an identity physical start and a model z+10°
delta, these two paths yield physical x−10° and z+10° respectively. Training
export conventions for the deployed checkpoint have not been verified; do not
blindly swap output quaternion elements to compensate.

The inspected RoboTwin `aloha-agilex` asset references an ARX5 URDF and applies
`global_trans_matrix=diag(1,-1,-1)` to its end-link orientation. Its pose getter
uses `gripper_bias - 0.12 = 0` positional offset. These asset definitions do not
establish a PiPER flange-to-policy-EEF calibration. The current no-transform,
zero-TCP configuration remains an unverified physical frame assumption.

Offline checks reject the recorded starts at joint boundaries. A hypothetical
interior start can pass the limited first-step IK check for both arms, while
other interior starts fail the local joint-step budget. This does not certify
any starting pose, collision clearance, or the full unmodified prediction.
Artifacts and a reproduction script are in
`results/real-aloha/action-contract-20261008/`; run `reproduce.py` from the project
root. No server changes or robot motion were made during this audit.

Continuous execution (`--max_chunks > 1`) requires the server metadata to declare
`executed_action_history`; otherwise the client rejects before reset/inference.
The inspected Ascend service does not declare this capability. Its code and
running process are left unchanged. Single-chunk commissioning can use
`--max_chunks 1 --max-eef-actions 1`; this does not establish continuous task
execution, and all hardware readiness checks still apply.

Read-only readiness inspection, without enabling, resetting or switching mode:

```bash
python -m evaluation.real_aloha.piper_preflight --output /tmp/piper-preflight.json
```

The inspected site's two arms report `S-V1.8-8`; the execution entry now defaults
to `--firmware-version v188` and passes that profile to `PiperController`.
Use `--max-eef-actions 1` for a single confirmed EEF arrival. This limits the
number of actions, **not their distance**: inspect the predicted target before
commissioning. Partial measured samples are saved as
`eef_measured_partial_*.npy` and are not padded or sent to the model KV cache.
The default `--max-eef-actions 0` retains complete-chunk execution.
For commissioning, explicitly add `--eef-step-limit 0.004
--eef-rotation-limit 0.025 --hold-grippers` to limit each flange target to
4 mm / 0.025 radians from fresh feedback and send no gripper commands.
An arm-only terminal single-step probe saves `eef_final_flange.npy` and does
not require gripper telemetry or submit incomplete action history.
These limits alter the requested trajectory; arrival proves a limited control
step, not faithful full-trajectory execution or task success. Raw model chunks
remain in `eef_prediction_*.npy`, while `eef_commands.jsonl` records the actual
limited targets. The project client disconnects without clearing the site's
operator stop flag.

The migrated EEF path now checks fresh `get_arm_status()` and `get_flange_pose()`
feedback before sending. After sending, it requires newer feedback to confirm
both flange targets within 2 mm and 0.02 rad, with a 3 s timeout. Nonzero arm status,
stale/missing feedback, a runtime stop, or timeout aborts the chunk. If a command
was already attempted, electronic emergency stop is requested on both arms,
without the legacy wrapper's automatic reset. Recovery must resolve the reported
condition; the client does not clear the hardware stop latch automatically.

**Electronic emergency stop is damped stopping, not position holding.** The
[official PiPER API](https://github.com/agilexrobotics/pyAgxArm/blob/master/docs/piper/piper_api.md#electronic-emergency-stop--electronic_emergency_stop)
states that raised joints can slowly descend under damping. `reset()` and
`disable()` can instead cause immediate dropping. On 2026-10-08 the onsite
operator confirmed wrist descent after the rejected commissioning command and
later manual reset/homing. The installed v188 SDK sends `ArmMsgMotionCtrl(1)`
for electronic stop; the project's exception path does not call reset/disable.
This supports damped descent as the explanation for the observed post-stop
movement, but no continuous fault-time trace exists to attribute every part of
the displacement. An enabled-driver bit alone does not prove position holding.
Do not remove the fault stop or replace it with an unverified holding command.
Commissioning must account for descent clearance and a valid nonsingular start.

`eef_commands.jsonl` records target, pre-send pose, send attempt, confirmed arrival,
and errors. `reached_step` counts confirmed flange arrivals, not send calls or
successful grasps. A send attempt may partly succeed before failing. Gripper
contact and task success remain unverified. This adds point-by-point arrival
waiting and can be slower than the old nominal 50 Hz streaming loop. It does not
change target coordinates, add TCP offsets, or make unreachable predictions valid.

### Explicit Piper contract and synchronized collection

`profiles/piper_site_candidate.json` is a separate **candidate** Piper profile.
It records camera topics, CAN names, firmware, feedback units, gripper range,
base/TCP transforms and timestamp tolerances. The copied site geometry (right
base y=-0.612 m and TCP z=0.1425 m) is **not calibrated by this change**.
Its new contract uses physical xyzw relative rotations and episode-initial
positions. It is not the legacy `robotwin_eval` quaternion interpretation.
Do not feed actions from that legacy convention into this profile unchanged.

Read-only recording on the industrial PC, from the project root:

```bash
source /opt/ros/noetic/setup.bash
LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libffi.so.7 \
/home/agilex/miniconda3/envs/piper_new/bin/python -m evaluation.real_aloha.piper_capture \
  --profile evaluation/real_aloha/profiles/piper_site_candidate.json \
  --output /home/agilex/zero-wam-readonly/piper-session-001 --seconds 10
```

This subscribes to ROS and reads SDK feedback directly; it never constructs the
site's enabling `PiperController`, enables drivers, resets, homes, changes mode,
or sends targets. It requires working ROS cameras and configured CAN interfaces.
It preserves original RGB PNGs and all three ROS header timestamps, plus each
arm's flange, joint, gripper and status feedback timestamps. Every accepted
sample must be fresh, have bounded cross-source timestamp skew, and advance all
source timestamps. This is software timestamp alignment, not a hardware trigger.
A failed or interrupted capture preserves `capture.json` and any partial samples.
An existing output directory is rejected to prevent mixing recordings.
The `LD_PRELOAD` above matches this industrial PC's interactive shell setup:
noninteractive SSH otherwise fails during CvBridge conversion with a
`ffi_type_pointer` symbol error. This path is specific to that PC.

To record control targets during separately operated/authorized control, also
pass `--command-log /path/to/eef_commands.jsonl`. The project's
`run_piper_eef.py` now records `sent_at` and `completed_at` without changing its
motion behavior. Records indicate a send attempt, which may only partly succeed.
Old logs without send timestamps are reported as incompatible; missing gripper
targets remain missing. Measured poses are never silently promoted to command
labels. The site's separate `real_robot_inference_zero_wam.py` has not been
modified; its logs need the same timestamped schema before they can be joined.
No control program is launched by the recorder.

Offline audit, with no SDK/ROS dependency:

```bash
python -m evaluation.real_aloha.piper_audit /path/to/piper-session-001
# Optionally test a deliberately selected candidate statistics file:
python -m evaluation.real_aloha.piper_audit /path/to/piper-session-001 \
  --stats /path/to/action_stats.json
```

The audit validates timestamps/images and the captured profile digest, then
checks flange -> task TCP -> episode-relative EEF -> optional quantile
normalization -> reconstruction -> flange. It reports geometric rotation error,
not Euler-coordinate differences. `audit.json` distinguishes measured-state
roundtrips, available command labels, post-completion feedback comparisons,
normalization coverage and controller status. `measured_relative_actions.npy`
contains **feedback-derived** trajectories, not issued targets. A successful
roundtrip does not validate physical calibration, model accuracy, tracking or
collision safety. No statistics are fitted from a stationary recording.

Independent inference entry:

```bash
python -m evaluation.real_aloha.piper_server --profile /path/to/selected-profile.json --check-only
# Once explicitly configured, launch using the existing server's other arguments:
python -m evaluation.real_aloha.piper_server --profile /path/to/selected-profile.json --port 29056
```

The supplied candidate intentionally cannot start inference: `normalization`,
`model_action_dt_s` and `model_actions_per_latent` are unset. A selected profile
must specify `normalization` as `{ "path": "action_stats.json", "sha256": "...",
"action_convention": "episode_relative_physical_xyzw" }`, plus a positive
`model_video_fps`, `model_action_dt_s` and integer `model_actions_per_latent`.
The entry checks `actions_per_latent * action_dt = 4 / video_fps` for the model's
four-frame temporal VAE compression. For example, a **dataset** with 30 Hz
actions and 15 fps videos has 8 actions/latent; that example is not a validated
Piper deployment setting. Sampling ROS at 10 Hz for a diagnostic recording does
not establish the model's action period. There is no implicit RoboTwin stats or
16-step fallback. The normal production entry and running service are unchanged.

Commissioning sequence: first record stationary feedback, then separately
collect operator-controlled small translations, rotations and gripper changes
with timestamped commands. Validate the geometry/timing against independent
physical measurements before using such trajectories as training labels.
As of 2026-10-09, software tests pass. The site's CAN script has verified
`can_left/right` UP at 1 Mbps (USB 1-12 to right, 1-13 to left), and ROS cameras
have been restored. Live collection still yielded zero accepted samples:
arm feedback was missing, both interfaces had zero RX packets, and a separate
three-second passive listen on each bus received no frames. Check arm power and
physical CAN connections before retrying. This evidence does not distinguish
power from cabling issues. No live pose/command roundtrip is claimed, and no
robot motion commands were sent.

Subsequent CAN diagnosis found both adapters present as `can0/1`, DOWN.
Running the site's existing activation script restored `can_right/left` at
1 Mbps; passive five-second recordings then received approximately 1000 frames
per high-rate feedback ID per arm. Three SDK reads one second apart returned
fresh flange, joint, gripper and status feedback on both arms (under 5 ms old),
with NORMAL/STANDBY status and no reported joint communication faults. Interface
TX counts remained zero. This resolves the current CAN-reading blocker; it does
not retroactively establish why the earlier UP interfaces received no frames.
After reboot or adapter reconnection, check the interfaces before collecting:

```bash
sudo bash /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/can_multi_activate.sh
ip -details -statistics link show type can
```

The script uses the site's fixed USB-port mapping; moving adapters to different
USB ports requires verifying that mapping again. No automatic startup service
was installed by this diagnosis.

Read-only gripper parameter queries on 2026-10-09 found a separate mismatch:
the installed pyAgxArm 1.0.0 reports width in metres, with firmware-configured
maximum ranges of 0.07 m (left) and 0.10 m (right), teaching ratios both 100%.
Measured feedback was 0.07014 m and 0.09960 m. The site's controller clamps
outgoing gripper commands to 0.08 m for both arms; this software cap is not a
measurement of either gripper's physical full opening. In particular, a
0.09960 m right-arm reading does not imply a metre/millimetre conversion bug.
Firmware parameters alone do not establish physical opening or zero calibration.
The candidate profile and motion limits remain unchanged pending physical
verification; do not normalize by the old shared 0.08 m value or silently clip
the left feedback's 0.14 mm excess over its firmware-configured range.
The successful 51-sample stationary capture is
`/home/agilex/zero-wam-readonly/piper-contract-capture-20261009-e` on the PC;
its full audit fails explicitly on the gripper range mismatch.

### RoboCOIN Split ALOHA dataset inference

`python -m evaluation.real_aloha.aloha_dataset_server` explicitly selects the
`agilex_cobot_decoupled_magic` dataset profile in `wan_va/configs/va_aloha_cfg.py`.
Set `MODEL_PATH` to the intended pretrain checkpoint before launching. It uses
the bundled `robocoin_split_aloha.json` statistics (SHA256
`71ebc527638378e36d5b1d15fd552fa8d97adfbfc320192942bddbce448073c5`), physical
episode-relative EEF/xyzw actions, and absolute gripper scale labels. The selected
sample's timing is 30 Hz actions, 15 fps video, and 8 actions per temporal latent.
The 30-channel model layout and selected 16-channel bimanual EEF output are
preserved. Existing model architecture/view layout comes from the tested
three-view inference base; this profile does not certify all training settings
or the physical robot's camera/TCP calibration.

The endpoint defaults to `127.0.0.1:29058` for offline requests. It does not
change the old service on port 29056, start a robot client, or set physical
gripper travel/zero values. In particular, do not use this profile with a
legacy `robotwin_eval` quaternion adapter without explicitly reconciling the
rotation convention. Dataset statistics are not a hardware calibration.

For one-chunk diagnostic recording, set `ZERO_WAM_TRACE_ROOT` before starting
`aloha_dataset_server`, and include a unique `trace_name` in the reset request.
The recorder preserves the selected profile, writes original observations,
preprocessed input, demo latent, normalization, and each video/action scheduler
step to that run directory. `completion.json` reports completed step counts;
`denoising_trace.pt` is saved even after an inference failure. Reusing a run name
is rejected. Intermediate noisy tensors are diagnostic data, not executable
robot actions. An optional `diagnostic_seed` in the infer request fixes the RNG.
Video previews require a separate VAE decode; saving tensors alone does not
produce an MP4 automatically.

An explicitly recorded reset may additionally set `diagnostic_future_path` to
an offline NPZ containing four future RGB frames for each configured camera.
This is an intervention experiment: the encoded future replaces the final
video denoising result before the zero-noise cache update, and the resulting
actions are conditioned on that supplied video. It is never enabled by default.
`diagnostic_oracle_future` in the saved config marks these runs; the supplied
future and injected step are saved. A repeated-current-frame future is synthetic
stationary conditioning, not a recording of a successful task. Its decoded
video must not be reported as model-generated future motion.

### Saved mean starting posture (2026-10-10)

[`profiles/robocoin_mean_start_20261010.json`](profiles/robocoin_mean_start_20261010.json)
records the per-episode mean initial joint angles from 408 RoboCOIN Split ALOHA
trajectories and the actual reached angles on this Piper pair. It uses degrees
in J1–J6 order, with separate `can_left` and `can_right` entries. The posture was
reached with onsite supervision, one arm at a time, at 1% speed, after confirming
stable CAN/MOVE_J feedback. It preserves the grippers and does not recalibrate
joint zero offsets. This is a reusable posture record, not an automatic startup
command or a verified path from arbitrary current poses. Inference quality at
this posture is assessed separately. Experiment records are under
`results/real-aloha/mean-pose-v2-20261010/` locally and
`/data5T/d00942093/zero-wam/Zero-WAM/outputs/mean-pose-v2-20261010/` on the server.

### Held-out bread demonstrations (2026-10-12)

See [BREAD_EVAL.md](BREAD_EVAL.md) for prepared original/landscape test videos,
the explicit training-contract template, fresh observation capture, one-command
offline prediction/video export, paired checkpoint comparison and a dedicated
test-demo server. Training demos `bread_000/002/003/004` are excluded from the
test registry; only `bread_test000/001` are selected. The existing hardware
client is not launched or certified by this preparation.
