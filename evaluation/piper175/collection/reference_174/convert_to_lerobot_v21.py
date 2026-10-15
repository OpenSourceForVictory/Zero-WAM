#!/usr/bin/env python3
"""
Convert ALOHA HDF5 datasets to LeRobot v2.1 format.

Usage:
    # Same prompt for all episodes
    python convert_to_lerobot_v21.py --input_dir ./aloha_mobile_dummy --output_dir ./lerobot_output --fps 30 --prompt "Pick up the red block and place it in the box"

    # Per-episode prompts from a JSON file
    python convert_to_lerobot_v21.py --input_dir ./aloha_mobile_dummy --output_dir ./lerobot_output --fps 30 --prompts_file prompts.json

    prompts.json format:
    {
        "0": "Pick up the red block",
        "1": "Open the drawer",
        "2": "Place the cup on the plate",
        "3": "Close the box"
    }

Directory layout produced (v2.1):
    output/
    ├── data/chunk-000/episode_XXXXXX.parquet
    ├── videos/chunk-000/<camera_key>/episode_XXXXXX.mp4
    └── meta/
        ├── info.json
        ├── episodes.jsonl
        ├── tasks.jsonl
        └── episodes_stats.jsonl
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import av
import h5py
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.spatial.transform import Rotation

sys.path.insert(0, "/home/agilex/cobot_magic/piper_sdk")
from piper_sdk import C_PiperForwardKinematics


def parse_args():
    p = argparse.ArgumentParser(description="Convert ALOHA HDF5 to LeRobot v2.1")
    p.add_argument("--input_dir", default=".", help="Directory with HDF5 episode files")
    p.add_argument("--output_dir", default="./lerobot_dataset", help="Output root")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--task", default=None, help="Short task name (fallback if no prompt)")
    p.add_argument("--prompt", default=None, help="Task instruction prompt applied to all episodes")
    p.add_argument("--prompts_file", default=None,
                   help="JSON file mapping episode_index (str) -> prompt text, for per-episode prompts")
    p.add_argument("--robot_type", default="aloha_mobile")
    p.add_argument("--video_codec", default="mpeg4")
    p.add_argument("--remove-video", action="store_true",
                   help="After converting each episode, remove video data from the original HDF5 file to save disk space")
    return p.parse_args()


def resolve_prompts(args, num_episodes):
    """Return a list of prompt strings, one per episode.

    Priority: --prompts_file > --prompt > --task > default
    Also returns the number of unique tasks.
    """
    if args.prompts_file:
        with open(args.prompts_file, "r") as f:
            mapping = json.load(f)
        prompts = []
        for i in range(num_episodes):
            key = str(i)
            if key not in mapping:
                print(f"Warning: prompts_file missing episode {i}, using default prompt")
                prompts.append("Manipulation task")
            else:
                prompts.append(mapping[key])
        return prompts

    single = args.prompt or args.task or "Manipulation task"
    return [single] * num_episodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_hdf5_files(input_dir):
    """Recursively find *.hdf5 files, sorted by episode number."""
    files = []
    for root, _, fnames in os.walk(input_dir):
        for f in fnames:
            if f.endswith(".hdf5"):
                files.append(os.path.join(root, f))
    if not files:
        return []
    files.sort(key=lambda f: int(re.search(r"(\d+)", os.path.basename(f)).group(1)))
    return files


def compute_eff_pose(qpos, fk, left_slice=slice(0, 6), right_slice=slice(7, 13)):
    """从关节角度计算双臂末端位姿。返回 (left_pose, right_pose)，shape=(T, 7)，单位 xyz=mm, quat=[qx,qy,qz,qw]。"""
    T = qpos.shape[0]
    left_pose = np.zeros((T, 7), dtype=np.float32)
    right_pose = np.zeros((T, 7), dtype=np.float32)
    for t in range(T):
        l_result = fk.CalFK(qpos[t, left_slice].tolist())[-1]
        r_result = fk.CalFK(qpos[t, right_slice].tolist())[-1]
        # xyz 保持不变，rpy(度)→四元数
        left_pose[t, :3] = l_result[:3]
        left_pose[t, 3:] = rpy_deg_to_quat(l_result[3:6])
        right_pose[t, :3] = r_result[:3]
        right_pose[t, 3:] = rpy_deg_to_quat(r_result[3:6])
    return left_pose, right_pose


def rpy_deg_to_quat(rpy_deg):
    """将 RPY (ZYX欧拉角, 度) 转为四元数 [qx, qy, qz, qw]。
    输入: [roll, pitch, yaw] 单位度，ZYX欧拉角约定
    输出: [qx, qy, qz, qw] 四元数
    """
    # scipy Rotation.from_euler 的 'ZYX' 对应 yaw, pitch, roll
    r = Rotation.from_euler('ZYX', [rpy_deg[2], rpy_deg[1], rpy_deg[0]], degrees=True)
    return r.as_quat()  # [qx, qy, qz, qw]


def pose6d_to_quat_pose(pose6d):
    """将 6-dim [x,y,z,rx,ry,rz] (mm, degree) 转为 7-dim [x,y,z,qx,qy,qz,qw]"""
    pose7d = np.zeros((pose6d.shape[0], 7), dtype=np.float32)
    pose7d[:, :3] = pose6d[:, :3]
    for t in range(pose6d.shape[0]):
        pose7d[t, 3:] = rpy_deg_to_quat(pose6d[t, 3:6])
    return pose7d


def probe_episode(path):
    """Return (num_frames, cam_keys, state_dim, action_dim, base_action_dim, img_h, img_w,
                has_left_eff_pose, has_right_eff_pose, eff_pose_dim)."""
    with h5py.File(path, "r") as f:
        action = f["action"]
        qpos = f["observations/qpos"]
        num_frames = action.shape[0]

        base_action_dim = 0
        if "base_action" in f:
            base_action_dim = f["base_action"].shape[-1]

        cam_keys = sorted(f["observations/images"].keys())
        first_cam = cam_keys[0]
        img_h, img_w = f[f"observations/images/{first_cam}"].shape[1:3]

        has_left_eff_pose = "observations/left_eff_pose" in f
        has_right_eff_pose = "observations/right_eff_pose" in f
        eff_pose_dim = 0
        if has_left_eff_pose:
            eff_pose_dim = f["observations/left_eff_pose"].shape[-1]
        elif has_right_eff_pose:
            eff_pose_dim = f["observations/right_eff_pose"].shape[-1]

        return (num_frames, cam_keys, qpos.shape[-1], action.shape[-1], base_action_dim,
                img_h, img_w, has_left_eff_pose, has_right_eff_pose, eff_pose_dim)


def encode_video_mp4(images: np.ndarray, out_path: str, fps: int, codec: str = "mpeg4"):
    """Write (T, H, W, 3) uint8 array → mp4."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    container = av.open(out_path, mode="w")
    stream = container.add_stream(codec, rate=fps)
    stream.width = images.shape[2]
    stream.height = images.shape[1]
    stream.pix_fmt = "yuv420p"

    for img in images:
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for pkt in stream.encode(frame):
            container.mux(pkt)
    for pkt in stream.encode():
        container.mux(pkt)
    container.close()


def compute_stats(arr: np.ndarray):
    """Return {min, max, mean, std} per dimension."""
    return {
        "min": np.min(arr, axis=0).tolist(),
        "max": np.max(arr, axis=0).tolist(),
        "mean": np.mean(arr, axis=0).tolist(),
        "std": np.std(arr, axis=0).tolist(),
    }


def remove_video_from_hdf5(filepath):
    """删除 HDF5 文件中 observations/images/ 下的视频数据，保留其他所有数据集。"""
    saved_data = {}
    images_keys = []

    with h5py.File(filepath, 'r') as f:
        def collect(name, obj):
            if isinstance(obj, h5py.Dataset):
                if name.startswith('observations/images/'):
                    images_keys.append(name)
                else:
                    saved_data[name] = obj[()]
        f.visititems(collect)

    if not images_keys:
        return

    old_size = os.path.getsize(filepath)

    tmp_path = filepath + '.tmp'
    with h5py.File(tmp_path, 'w') as f:
        for name, data in saved_data.items():
            group_path = '/'.join(name.split('/')[:-1])
            if group_path:
                f.require_group(group_path)
            f.create_dataset(name, data=data)
        # 保留空的 images group 以维持目录结构
        f.require_group('observations/images')

    os.replace(tmp_path, filepath)

    new_size = os.path.getsize(filepath)
    print(f"    视频已从原始文件删除: {old_size / (1024**3):.2f} GB -> {new_size / (1024**2):.2f} MB")


def to_video_key(cam_key: str) -> str:
    """e.g. 'cam_high' -> 'observation.images.cam_high'."""
    return f"observation.images.{cam_key}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    hdf5_files = find_hdf5_files(args.input_dir)
    if not hdf5_files:
        print(f"No .hdf5 files found in {args.input_dir}")
        sys.exit(1)
    print(f"Found {len(hdf5_files)} episodes")

    # ---- resolve per-episode prompts ----
    prompts = resolve_prompts(args, len(hdf5_files))
    # build unique task list and per-episode task_index
    unique_tasks = []
    task_to_idx = {}
    ep_task_indices = []
    for p in prompts:
        if p not in task_to_idx:
            task_to_idx[p] = len(unique_tasks)
            unique_tasks.append(p)
        ep_task_indices.append(task_to_idx[p])
    total_tasks = len(unique_tasks)
    print(f"  {total_tasks} unique task prompt(s)")

    # ---- probe first episode to learn schema ----
    (num_frames_0, cam_keys, state_dim, action_dim, base_dim, img_h, img_w,
     has_left_eff_pose, has_right_eff_pose, eff_pose_dim) = probe_episode(hdf5_files[0])
    # action 直接使用 HDF5 中的 action (14-dim: 左臂7 + 右臂7)，不再拼接 base_action
    print(f"  state_dim={state_dim}, action_dim={action_dim}")
    print(f"  cameras={cam_keys}, image={img_w}x{img_h}, fps={args.fps}")
    print(f"  has_left_eff_pose={has_left_eff_pose}, has_right_eff_pose={has_right_eff_pose}")

    # 创建FK计算器（如果没有末端位姿数据则用于计算）
    fk = None
    compute_fk = not (has_left_eff_pose and has_right_eff_pose)
    if compute_fk:
        fk = C_PiperForwardKinematics(dh_is_offset=0x01)
        print("  将使用CalFK从关节角度计算末端位姿")

    # ---- output dirs ----
    out = Path(args.output_dir)
    data_dir = out / "data" / "chunk-000"
    vid_dir = out / "videos" / "chunk-000"
    meta_dir = out / "meta"
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    # ---- build parquet schema once ----
    action_dim_out = 28  # [left_eff_pose(7), right_eff_pose(7), left_joint(6), right_joint(6), left_gripper(1), right_gripper(1)]
    fields = [
        pa.field("action", pa.list_(pa.float32())),
    ]
    for ck in cam_keys:
        fields.append(pa.field(to_video_key(ck), pa.struct([
            pa.field("path", pa.string()),
            pa.field("timestamp", pa.float64()),
        ])))
    fields += [
        pa.field("timestamp", pa.float64()),
        pa.field("episode_index", pa.int64()),
        pa.field("frame_index", pa.int64()),
        pa.field("index", pa.int64()),
        pa.field("next.done", pa.bool_()),
        pa.field("task_index", pa.int64()),
    ]
    schema = pa.schema(fields)

    episodes_meta = []
    episodes_stats = []
    global_idx = 0

    skipped_episodes = []

    for ep_idx, hdf5_path in enumerate(hdf5_files):
        print(f"  [{ep_idx+1}/{len(hdf5_files)}] {os.path.basename(hdf5_path)}", end="")
        print(f"  prompt=\"{prompts[ep_idx]}\"", end="")

        try:
            with h5py.File(hdf5_path, "r") as f:
                qpos = f["observations/qpos"][:]
                imgs = {ck: f[f"observations/images/{ck}"][:] for ck in cam_keys}

                # 读取或计算末端位姿，统一转为 7-dim [x,y,z,qx,qy,qz,qw]
                has_left = "observations/left_eff_pose" in f
                has_right = "observations/right_eff_pose" in f
                if has_left:
                    left_raw = f["observations/left_eff_pose"][:].astype(np.float32)
                    # 如果原始是 6-dim [x,y,z,r,p,y]，转换为 7-dim quaternion
                    if left_raw.shape[-1] == 6:
                        left_eff_pose = pose6d_to_quat_pose(left_raw)
                    else:
                        left_eff_pose = left_raw
                if has_right:
                    right_raw = f["observations/right_eff_pose"][:].astype(np.float32)
                    if right_raw.shape[-1] == 6:
                        right_eff_pose = pose6d_to_quat_pose(right_raw)
                    else:
                        right_eff_pose = right_raw
                if fk is not None:
                    if not has_left or not has_right:
                        fk_left, fk_right = compute_eff_pose(qpos, fk)
                        if not has_left:
                            left_eff_pose = fk_left
                        if not has_right:
                            right_eff_pose = fk_right
        except Exception as e:
            print(f"  ⚠ 跳过损坏文件: {e}")
            skipped_episodes.append(ep_idx)
            continue

        T = qpos.shape[0]
        timestamps = np.arange(T, dtype=np.float64) / args.fps

        # ---- encode videos ----
        vid_rel_paths = {}
        for ck in cam_keys:
            vk = to_video_key(ck)
            vid_rel = f"videos/chunk-000/{vk}/episode_{ep_idx:06d}.mp4"
            encode_video_mp4(imgs[ck], str(out / vid_rel), args.fps, args.video_codec)
            vid_rel_paths[ck] = vid_rel

        # ---- build action: [left_eff_pose(7), right_eff_pose(7), left_joint(6), right_joint(6), left_gripper(1), right_gripper(1)] ----
        left_joint = qpos[:, 0:6]      # (T, 6)
        right_joint = qpos[:, 7:13]    # (T, 6)
        left_gripper = qpos[:, 6:7]    # (T, 1)
        right_gripper = qpos[:, 13:14]  # (T, 1)
        action_arr = np.concatenate([left_eff_pose, right_eff_pose, left_joint, right_joint, left_gripper, right_gripper], axis=-1)  # (T, 28)

        # ---- build column arrays ----
        act_arr = pa.array([row.tolist() for row in action_arr], type=pa.list_(pa.float32()))
        ts_arr = pa.array(timestamps.tolist(), type=pa.float64())
        ep_arr = pa.array([ep_idx] * T, type=pa.int64())
        fr_arr = pa.array(list(range(T)), type=pa.int64())
        idx_arr = pa.array(list(range(global_idx, global_idx + T)), type=pa.int64())
        done_arr = pa.array([False] * (T - 1) + [True], type=pa.bool_())
        task_arr = pa.array([ep_task_indices[ep_idx]] * T, type=pa.int64())

        col_dict = {
            "action": act_arr,
            "timestamp": ts_arr,
            "episode_index": ep_arr,
            "frame_index": fr_arr,
            "index": idx_arr,
            "next.done": done_arr,
            "task_index": task_arr,
        }
        for ck in cam_keys:
            vk = to_video_key(ck)
            vf_list = [{"path": vid_rel_paths[ck], "timestamp": float(timestamps[t])} for t in range(T)]
            col_dict[vk] = pa.array(vf_list, type=pa.struct([
                pa.field("path", pa.string()),
                pa.field("timestamp", pa.float64()),
            ]))

        table = pa.table(col_dict, schema=schema)
        pq.write_table(table, data_dir / f"episode_{ep_idx:06d}.parquet")

        # ---- stats ----
        ep_stats = {
            "action": compute_stats(action_arr),
        }
        episodes_stats.append({
            "episode_index": ep_idx,
            "stats": ep_stats,
        })
        episodes_meta.append({
            "episode_index": ep_idx,
            "tasks": [prompts[ep_idx]],
            "length": T,
        })

        global_idx += T
        print(f"  ({T} frames)")

        # ---- remove video data from original HDF5 to save space ----
        if args.remove_video:
            remove_video_from_hdf5(hdf5_path)

    total_frames = global_idx

    # ---- info.json ----
    features = {
        "action": {
            "dtype": "float32",
            "shape": [action_dim_out],
            "names": [
                "left_x", "left_y", "left_z", "left_qx", "left_qy", "left_qz", "left_qw",
                "right_x", "right_y", "right_z", "right_qx", "right_qy", "right_qz", "right_qw",
                "left_joint_0", "left_joint_1", "left_joint_2", "left_joint_3", "left_joint_4", "left_joint_5",
                "right_joint_0", "right_joint_1", "right_joint_2", "right_joint_3", "right_joint_4", "right_joint_5",
                "left_gripper", "right_gripper",
            ],
        },
    }
    for ck in cam_keys:
        vk = to_video_key(ck)
        features[vk] = {
            "dtype": "video",
            "shape": [img_h, img_w, 3],
            "names": ["height", "width", "channel"],
            "info": {
                "video.fps": args.fps,
                "video.codec": args.video_codec,
                "video.height": img_h,
                "video.width": img_w,
            },
        }

    info = {
        "codebase_version": "v2.1",
        "robot_type": args.robot_type,
        "total_episodes": len(hdf5_files),
        "total_frames": total_frames,
        "total_tasks": 1,
        "fps": args.fps,
        "splits": {"train": f"0:{len(hdf5_files)}"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": features,
    }

    with open(meta_dir / "info.json", "w") as f:
        json.dump(info, f, indent=2)

    with open(meta_dir / "tasks.jsonl", "w") as f:
        f.write(json.dumps({"task_index": 0, "task": args.task}) + "\n")

    with open(meta_dir / "episodes.jsonl", "w") as f:
        for em in episodes_meta:
            f.write(json.dumps(em) + "\n")

    with open(meta_dir / "episodes_stats.jsonl", "w") as f:
        for es in episodes_stats:
            f.write(json.dumps(es) + "\n")

    print(f"\nDone -> {args.output_dir}")
    print(f"  {len(hdf5_files)} episodes, {total_frames} frames, {len(cam_keys)} cameras")


if __name__ == "__main__":
    main()
