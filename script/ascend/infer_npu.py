"""Run the released ICL checkpoint against recorded RoboTwin observations."""
import argparse
import copy
import json
import os
from pathlib import Path
import time

import imageio.v2 as imageio
import numpy as np
import torch
import torch_npu
from diffusers.video_processor import VideoProcessor
from diffusers.utils import export_to_video
from wan_va.configs.va_robotwin_cfg import va_robotwin_cfg
from wan_va.wan_va_server import VA_Server
from wan_va.utils import init_logger

ROOT = Path(__file__).resolve().parents[2]


def read_observation(directory, keys, frame=0):
    obs = {}
    for key in keys:
        video = directory / f'{key}.mp4'
        with imageio.get_reader(
            str(video), format='ffmpeg'
        ) as reader:
            obs[key] = reader.get_data(frame).copy()
    return obs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=ROOT/'models/zero-wam-posttrain-robotwin')
    parser.add_argument('--input', type=Path, default=ROOT/'data/place_object_scale')
    parser.add_argument('--icl-latent', type=Path, default=ROOT/'data/place_object_scale_icl.pth')
    parser.add_argument('--output', type=Path, default=ROOT/'outputs/place_object_scale')
    parser.add_argument('--steps', type=int, default=50)
    parser.add_argument('--chunks', type=int, default=1)
    parser.add_argument('--device', default='npu:0')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--prompt', default='Use one arm to grab the object and put it on the scale.')
    parser.add_argument('--no-video', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=29056)
    args = parser.parse_args()
    if args.steps < 1 or args.chunks < 1:
        parser.error('steps and chunks must be positive')
    init_logger()
    torch.set_num_threads(int(os.environ.get('OMP_NUM_THREADS', '8')))
    torch.npu.set_device(args.device)
    torch.manual_seed(args.seed)
    torch.npu.manual_seed_all(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(va_robotwin_cfg)
    config.model_path = str(args.model)
    config.device = args.device
    config.local_rank = int(args.device.split(':')[-1])
    config.rank = 0
    config.world_size = 1
    config.enable_offload = False
    config.save_root = str(args.output)
    config.num_inference_steps = args.steps
    config.action_num_inference_steps = args.steps
    started = time.monotonic()
    print('Loading checkpoint', args.model, flush=True)
    model = VA_Server(config)
    torch.npu.synchronize()
    load_seconds = time.monotonic() - started
    print(f'Model loaded in {load_seconds:.2f}s', flush=True)
    if args.serve:
        from wan_va.utils.Simple_Remote_Infer.deploy.websocket_policy_server import WebsocketPolicyServer
        # A single-NPU service does not need NCCL/HCCL or FSDP.
        WebsocketPolicyServer(model, host='127.0.0.1', port=args.port).serve_forever()
        return
    timings = []
    actions_all = []
    latents_all = []
    with torch.inference_mode():
        start = time.monotonic()
        model.infer(dict(reset=True, prompt=args.prompt, use_icl=True,
                         icl_latent_path=str(args.icl_latent),
                         video_guidance_scale=-1, icl_guidance_scale=5))
        torch.npu.synchronize()
        reset_seconds = time.monotonic() - start
        frame_cursor = 0
        for chunk in range(args.chunks):
            observation = read_observation(args.input, config.obs_cam_keys, frame_cursor)
            start = time.monotonic()
            action = model.infer({'obs': [observation]})['action']
            torch.npu.synchronize()
            elapsed = time.monotonic() - start
            if not np.isfinite(action).all():
                raise RuntimeError('Inference produced non-finite actions')
            latent = model.last_predicted_latents.detach().cpu()
            if not torch.isfinite(latent).all():
                raise RuntimeError('Inference produced non-finite video latents')
            np.save(args.output/f'actions_{chunk:03d}.npy', action)
            torch.save(latent, args.output/f'video_latents_{chunk:03d}.pt')
            actions_all.append(action)
            latents_all.append(latent)
            timings.append(elapsed)
            print(f'Chunk {chunk}: actions={action.shape}, latent={tuple(latent.shape)}, {elapsed:.2f}s', flush=True)
            if chunk + 1 < args.chunks:
                # Recorded-observation replay; these frames are not caused by
                # the predicted actions. It is not a closed-loop evaluation.
                count = 4 if chunk == 0 else 8
                frame_ids = [frame_cursor + (i+1)*config.action_per_frame//4 for i in range(count)]
                frame_cursor = frame_ids[-1]
                history = [read_observation(args.input, config.obs_cam_keys, i) for i in frame_ids]
                model.infer({'obs': history, 'compute_kv_cache': True})
        np.save(args.output/'actions.npy', np.concatenate(actions_all, axis=1))
        if not args.no_video:
            model.video_processor = VideoProcessor(vae_scale_factor=1)
            latent = torch.cat(latents_all, dim=2).to(args.device)
            video = model.decode_one_video(latent, 'np')[0]
            if not np.isfinite(video).all():
                raise RuntimeError('Decoded video is non-finite')
            export_to_video(video, str(args.output/'predicted_video.mp4'), fps=12)
    report = dict(
        device=args.device, torch=torch.__version__, torch_npu=torch_npu.__version__,
        seed=args.seed, steps=args.steps, chunks=args.chunks,
        model=str(args.model), input=str(args.input), icl_latent=str(args.icl_latent),
        mode='offline recorded-observation inference; no simulator or action execution',
        action_shape=list(np.concatenate(actions_all, axis=1).shape),
        action_layout='channels, latent_frames, actions_per_frame; first frame of first chunk is skipped by the official client',
        finite_actions=True, finite_video_latents=True,
        load_seconds=load_seconds, icl_cache_seconds=reset_seconds,
        chunk_seconds=timings,
        max_memory_allocated_gib=torch.npu.max_memory_allocated()/1024**3,
        max_memory_reserved_gib=torch.npu.max_memory_reserved()/1024**3,
    )
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__ == '__main__':
    main()
