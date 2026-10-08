# Split ALOHA / dual-Piper deployment

This adapter connects the released RoboTwin Zero-WAM policy to the fail-closed
ROS1 dual-Piper service in `/tmp/astra-manip-src`. The processes stay separate:

1. the ROS1 machine owns cameras, Piper feedback, calibrated FK/IK and commands;
2. this client owns Zero-WAM inference and action conversion;
3. the existing Ascend service owns the model.

The policy consumes `cam_high`, `cam_left_wrist`, and `cam_right_wrist` and
produces 16-D actions: `xyz + xyzw quaternion + opening` for each arm. As in the
RoboTwin evaluator, translation and rotation are relative to the EEF pose at
episode start. Translation is mapped through the measured
`policy_to_robot_rotation`; rotation deltas are post-multiplied onto the initial
EEF rotation.

## Preconditions

- Identify the exact Piper variant and use its matching official URDF.
- Restore and verify mechanical zero before trusting joint feedback.
- Measure the two arm-base transforms, TCPs, FK roots, workspace and joint limits
  in the control-service configuration.
- Verify all three 640x480 RGB topics and the left/right wrist assignment.
- Keep `/tmp/astra-manip-src/MOTION_INHIBITED` present until commissioning is
  complete. Neither this client nor the service enables or homes an arm.

The [official Piper SDK](https://github.com/agilexrobotics/piper_sdk) requires
correct CAN activation and baud rate. On a master/slave installation, disconnect
competing master commands and configure the slave mode as described in the
[official dual-Piper guide](https://github.com/agilexrobotics/piper_sdk/blob/master/asserts/double_piper.MD)
before accepting command topics. Select the matching model from the
[official URDF repository](https://github.com/agilexrobotics/agx_arm_urdf),
which has separate Piper, Piper H, Piper L and Piper X descriptions.

## 1. Start the observation service on the robot computer

Run inside its ROS Noetic environment. This is read-only and creates no command
publishers:

```bash
PYTHONPATH=/tmp python -m astra-manip-src serve \
  --backend piper_ros \
  --config /path/to/measured-piper.json \
  --output /tmp/zero-wam-observe-001 \
  --port 19140 \
  --task place_object_scale \
  --max-steps 1
```

Forward port 19140 over SSH when Zero-WAM runs on another computer. The service
binds loopback deliberately.

## 2. Create installation calibration

Copy `calibration.example.json`, measure the policy/task-frame axis mapping, and
set `verified` to `true`. Identity is valid only when the real task frame uses
the same forward/left/up axes as the evaluation scene. Do not infer this matrix
from a single arm pose.

## 3. Run shadow inference

Shadow mode sends no preview or execution request. It records the model action,
absolute requested EEF target and the 4 mm / 0.025 rad limited target.

```bash
python -m evaluation.real_aloha.run_policy \
  --mode shadow \
  --robot-host 127.0.0.1 --robot-port 19140 \
  --policy-host 127.0.0.1 --policy-port 39056 \
  --prompt "Use one arm to grab the object and put it on the scale" \
  --icl-id place_object_scale \
  --calibration evaluation/real_aloha/calibration.local.json \
  --output results/real-aloha/shadow-001 \
  --max-decisions 1 --max-actions 1
```

Inspect `action_0000.json`, camera recordings from the service, and the IK/FK
commissioning report before progressing.

## 4. Preview and commissioned motion

`preview` asks the control service for bounded IK but does not call `chunk_step`.
`execute` performs the service's preview-and-execute handshake. Both require a
motion-capable service with its measured configuration. Start with one action;
the service independently enforces 5 mm / 0.03 rad EEF limits, 0.01 rad joint
steps, workspace limits, fresh feedback and exact proposal identity.

```bash
python -m evaluation.real_aloha.run_policy ... \
  --mode preview --max-decisions 1 --max-actions 1

python -m evaluation.real_aloha.run_policy ... \
  --mode execute --max-decisions 1 --max-actions 1
```

Grippers are held at measured openings by default. Add `--follow-grippers` only
after gripper direction, range and contact-stop behavior have been commissioned.
