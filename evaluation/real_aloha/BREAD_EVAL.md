# Bread post-training evaluation

The user-designated split is **bread_000/002/003/004 for training** and
**bread_test000/001 for testing**. Never add the test videos to training,
checkpoint selection or a training cache. The manifest establishes the intended
split; it cannot prove what an independently running trainer has consumed.

The prepared package is `results/real-aloha/bread-eval-20261012/` locally and
`/data5T/d00942093/zero-wam/Zero-WAM/outputs/bread-eval-20261012/` on the inference host.

- `bundle/`: original held-out MP4s, SHA256 manifest and test-only registry.
- `landscape_bundle/`: explicit 90° counterclockwise rotation copies, full duration
  and 30 fps retained, H.264 CRF 18; originals are unchanged. Includes preview MP4s
  and six-point timeline images. This is an alternative, not an automatically
  selected replacement for whatever preprocessing training uses.
- `video_contact_sheet.jpg`: original orientation comparison for all six videos.
- `tools/`: standalone copies of the evaluation, capture, decode and tracing tools.

`bread_000` decodes landscape; the other five decode portrait with the table
rotated relative to it. Both held-out videos are complete: test000 is 46.2 s /
1386 frames and test001 is 30.3 s / 909 frames. Choose raw or rotated inputs to
match the actual training preprocessing, not merely whichever looks better.
Do not silently shorten or accelerate these videos: sampling FPS, context length,
rotation and resize must match training. Long videos can require substantially
more inference memory than the earlier short HumanGen examples.

Timeline review shows different tasks: test000 includes placing one plate and
bread on the tray, a cup/pitcher interaction, placing the cup on the tray and
moving the tray. Test001 focuses on the cup/pitcher interaction and moving the
tray with the cup. Per-demo descriptions are recorded in the manifest; do not
score both using the old two-plate instruction. Liquid transfer is not established
by the sampled images alone; physical success criteria need a full-video review.

## Once training finishes

1. Supply an exported inference checkpoint containing `transformer`, `vae`,
   `text_encoder` and `tokenizer`. Adapter-only weights require the training
   project's export/merge procedure first. Do not guess that procedure.
2. Fill `training_contract.template.json` from the training job: action convention,
   statistics file/hash, action period, actions per latent, video FPS, ICL sampling
   FPS/size/RoPE/guidance, and preprocessing. `count * action_dt == 4 / video_fps`.
   The current harness supports physical episode-relative XYZ, xyzw and absolute
   gripper labels with the existing 30-to-16 channel selection. A different
   trained representation requires an adapter before evaluation.
3. `demo_preprocessing=server_native_bilinear_no_extra_rotation` means the selected
   bundle is already in the intended orientation; the server samples and resizes
   it without another rotation/crop. Use this only when verified against training.
   No old normalization is chosen automatically. Previous ALOHA settings were
   8 actions/latent, 1/30 s action period, 15 fps robot video, 12 fps human video,
   320x480 human resize, RoPE 24 and guidance 5; these are historical reference
   values, not assumed post-training settings.
4. Prepare the bread scene on the robot, then acquire fresh images. Do not reuse
   the earlier water-bottle or document-bag observations as bread task results.

On the robot PC, with cameras already publishing (this script only subscribes):

```bash
PYTHONPATH=/opt/ros/noetic/lib/python3/dist-packages \
LD_LIBRARY_PATH=/opt/ros/noetic/lib \
/usr/bin/python3 capture_observation.py --output /tmp/bread_capture_UNIQUE
```

Copy its `observation.npz`, `manifest.json` and images to the inference host.
The script does not read arm state; record the actual joint/TCP/gripper state
separately and check it before any later physical trial. Do not move to a saved
posture automatically. The old mean posture is a reference, not a collision plan.

From the inference repository, using the server's Python environment:

```bash
cd /data5T/d00942093/zero-wam/Zero-WAM
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export ASCEND_RT_VISIBLE_DEVICES=6  # choose an actually available device
export PYTHON_BIN=/data5T/d00942093/miniforge3/envs/zero-wam/bin/python
bash outputs/bread-eval-20261012/tools/validate_bread.sh \
  /ABS/EXPORTED_CHECKPOINT /ABS/CONFIRMED_CONTRACT.json \
  /ABS/FRESH_CAPTURE/observation.npz \
  /ABS/SELECTED_BUNDLE /ABS/NEW_RESULT_DIRECTORY npu:0
```

This runs **two held-out demonstrations × five seeds**, one prediction chunk per
reset, with complete video/action denoising traces and MP4 previews. It never
connects to robot control. Raw input, sampled human frames, weights/statistics
hashes, effective model config, seeds, normalized and returned actions are saved.
The action report skips the padded first latent frame. Small displacement is not
task success. Cached VAE latents are not pre-generated before confirming the
training preprocessing and VAE; encoding happens at reset and is recorded.

Run the pretrain and posttrain checkpoints with the **same frozen fresh capture,
demo variant and seeds**, then compare:

```bash
PYTHONPATH=. "$PYTHON_BIN" outputs/bread-eval-20261012/tools/bread_eval.py compare \
  --before /ABS/PRETRAIN_RESULT --after /ABS/POSTTRAIN_RESULT \
  --output /ABS/NEW_COMPARISON.json
```

The comparison rejects mismatched images, demos, seeds, timing, configuration,
server source or trace source. Different checkpoint-specific normalization is
reported explicitly. This is an offline comparison, not a physical success rate.

## Preparing the live endpoint

The same validated configuration can expose the two test demo IDs on a dedicated
port. This starts an inference service only; it does not start the robot client.

```bash
PYTHONPATH=. "$PYTHON_BIN" outputs/bread-eval-20261012/tools/bread_eval.py serve \
  --checkpoint /ABS/EXPORTED_CHECKPOINT --contract /ABS/CONFIRMED_CONTRACT.json \
  --bundle /ABS/SELECTED_BUNDLE --output /ABS/NEW_SERVICE_LOG_DIRECTORY \
  --device npu:0 --host 127.0.0.1 --port 29059 --seeds 42 \
  --prompt 'Follow the task shown in the human demonstration.'
```

Demo IDs: `bread_test000`, `bread_test001`. Use a separate tunnel (e.g. local
39059 to server 29059) rather than silently replacing the old 39056 endpoint.
Each reset records the first chunk's full trace with a unique trial ID. Subsequent
execution/observation history must be recorded by the hardware client.

**Hardware integration is still gated by the final contract.** The existing
`run_piper_eef.py` currently assumes `(16,2,16)` and a shared 0.08 m gripper
scale, while earlier ALOHA tests used `(16,2,8)` and the site's gripper ranges
were not equivalent. Its startup enables arms. Do not launch it against new
weights merely because the offline endpoint works. Reconcile shape, timing,
physical frames/TCP, actual gripper calibration and metadata before a supervised
one-chunk test; then expand to measured-history closed loop. No change to that
existing control path is part of this video preparation.

For physical evaluation, predefine success for each demo after reviewing its
full video; record initial setup, all attempts, interventions, elapsed time,
completed chunks, robot video and failure reason in `trial_record.template.json`.
Keep aborted attempts in the denominator. Do not choose a seed because its first
action happens to be small. Reserve held-out videos for the locked evaluation;
repeated tuning on them turns them into validation data and needs a new test set.

## Absolute 28D endpoint and prediction video (2026-10-12)

For full joint chunks using the IPC's original execution flow, use
`run_piper_joint.py`; see [the copied client's instructions](merged_client/README.md).
It uses the original controller, smoothing, gripper and KV-cache loop.
The bounded probe below remains a separate diagnostic tool.

The newer server at `/mnt/sfs_turbo/lijianwen/Codes/Zero-WAM/start_infer.sh`
uses `evaluation.aloha.server`, checkpoint `aloha_step6000_validation/model`,
and `aloha_absolute_28_continuous_v2`. This differs from the earlier relative
16D harness described above. Joint channels are `[14:20]` (left) and `[20:26]`
(right), in radians. The first latent action block is padding.

`predict_joint_chunk.py` records a single inference without hardware access.
`run_piper_joint_probe.py` defaults to read-only controller checks; its optional
execution mode is a bounded, supervised arm-only probe, not full task execution.
The original 5-degree first-target gate was removed at the user's request.
The probe still checks its 10-degree total excursion, 3-degree adjacent-target
jumps, and controller joint limits. A rejected large initial transition requires
separate path assessment. Removing the first-target gate does not approve motion.

The server's opt-in `joint_video_trace.py` hook saves each prediction's input,
raw action, initial/predicted video latents and configuration. Set
`ZERO_WAM_PREDICTION_ROOT` before launch and call `install()` before constructing
the server. The inference math and cache contents are unchanged. Video decoding
runs separately, after inference, using the checkpoint VAE:

```bash
python joint_video_trace.py --trace /ABS/SESSION/chunk_000 --device npu:0
```

Decode from the inference repository with its environment (retain the Ascend
toolkit Python paths). The output includes three individual views, a composite
MP4, a 2-fps slow version, and an input/reconstruction/final-frame comparison.
For this prepared dataset, video timestamps follow 30-Hz source sampling with
stride 4 (7.5 fps). A two-latent first chunk decodes to only five video frames;
it is not a complete task rollout. Raw latents are saved automatically; MP4
export is a separate explicit decode step.
