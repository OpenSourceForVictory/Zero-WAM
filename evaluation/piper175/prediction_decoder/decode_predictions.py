"""Decode saved prediction latents without sending RPCs or robot commands."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--since', type=float, default=0, help='Minimum reset.json mtime')
    p.add_argument('--run', help='Decode only this run directory name')
    p.add_argument('--once', action='store_true')
    p.add_argument('--limit', type=int, default=0, help='Exit after N successful decodes; 0 unlimited')
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--device', default='cpu', choices=['cpu', 'cuda:0'])
    p.add_argument('--source-checkpoint', type=Path, help='Checkpoint path recorded in the remote latent metadata')
    a = p.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    lock = (a.root/'.cpu_decoder.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if a.device == 'cpu':
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
    import numpy as np
    import torch
    import imageio.v2 as iio
    import imageio_ffmpeg
    from diffusers import AutoencoderKLWan
    from diffusers.video_processor import VideoProcessor
    torch.set_num_threads(a.threads)
    vae = AutoencoderKLWan.from_pretrained(str(a.checkpoint/'vae'), torch_dtype=torch.float32).to(a.device).eval()
    processor = VideoProcessor(vae_scale_factor=1)
    failed = set()
    count = 0
    status_path = a.root/'decoder_status.json'
    print(f'{a.device} FP32 decoder ready', flush=True)

    def combine(run):
        clips = sorted(run.glob('chunk_*.mp4'))
        done = [x for x in clips if x.with_suffix('.decoded.json').exists()]
        if not done:
            return
        # Only publish the contiguous prefix. Never imply a missing chunk is present.
        prefix = []
        for idx, clip in enumerate(done):
            if clip.stem != f'chunk_{idx:05d}':
                break
            prefix.append(clip)
        if not prefix:
            return
        report = run/'prediction_combined.json'
        if report.exists() and json.loads(report.read_text()).get('chunks') == len(prefix):
            return
        listing = run/'prediction_concat.txt'
        listing.write_text(''.join(f"file '{x.name}'\n" for x in prefix))
        tmp = run/'prediction_combined.partial.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(tmp)], check=True)
        tmp.replace(run/'prediction_combined.mp4')
        atomic_json(report, {'chunks': len(prefix), 'frames': sum(json.loads(x.with_suffix('.decoded.json').read_text())['frames'] for x in prefix), 'updated_at': time.time(), 'note': 'Sequential predicted chunks; not wall-clock synchronized with robot video.'})

    while True:
        runs = sorted(a.root.glob('piperpika_*'), reverse=True)
        runs = [r for r in runs if r.is_dir() and (not a.run or r.name == a.run) and (r/'reset.json').exists() and (r/'reset.json').stat().st_mtime >= a.since]
        pending = [x for r in runs for x in sorted(r.glob('chunk_*.pt')) if not x.with_suffix('.decoded.json').exists() and x not in failed]
        if not pending:
            for run in runs:
                combine(run)
            atomic_json(status_path, {'state': 'idle', 'failed_this_process': len(failed), 'updated_at': time.time(), 'device': a.device, 'dtype': 'float32'})
            if a.once:
                break
            time.sleep(2)
            continue
        # Re-scan after each chunk so a new live run takes priority over old backlog.
        path = pending[0]
        started = time.monotonic()
        atomic_json(status_path, {'state': 'decoding', 'path': str(path), 'pending': len(pending), 'updated_at': time.time(), 'device': a.device, 'dtype': 'float32'})
        try:
            data = torch.load(path, map_location='cpu', weights_only=False)
            source_checkpoint = a.source_checkpoint or a.checkpoint
            matches = (Path(data['checkpoint']) == source_checkpoint if a.source_checkpoint
                       else Path(data['checkpoint']).resolve() == a.checkpoint.resolve())
            if not matches:
                raise ValueError('Checkpoint mismatch')
            latent = data['latent']
            if latent.ndim != 5 or latent.shape[0] != 1 or latent.shape[-1] % 3:
                raise ValueError(f'Unexpected three-view latent: {latent.shape}')
            if not torch.isfinite(latent).all():
                raise ValueError('Non-finite latent')
            views = []
            with torch.inference_mode():
                # Decode views sequentially to bound memory. Same normalization as VA_Server.
                for view in latent.chunk(3, dim=-1):
                    vae.clear_cache()
                    view = view.to(device=a.device, dtype=vae.dtype)
                    mean = torch.tensor(vae.config.latents_mean, device=a.device).view(1, vae.config.z_dim, 1, 1, 1)
                    std = torch.tensor(vae.config.latents_std, device=a.device).view(1, vae.config.z_dim, 1, 1, 1)
                    video = vae.decode(view * std + mean, return_dict=False)[0]
                    if not torch.isfinite(video).all():
                        raise ValueError('Non-finite decoded pixels')
                    rgb = processor.postprocess_video(video, output_type='np')[0]
                    views.append(np.rint(np.clip(rgb, 0, 1)*255).astype(np.uint8))
            combined = np.concatenate(views, axis=2)
            tmp = path.with_suffix('.partial.mp4')
            iio.mimwrite(str(tmp), combined, fps=data['video_fps'], codec='libx264', macro_block_size=1, ffmpeg_params=['-crf', '18', '-threads', '2'])
            tmp.replace(path.with_suffix('.mp4'))
            atomic_json(path.with_suffix('.decoded.json'), {'frames': len(combined), 'shape': list(combined.shape), 'fps': data['video_fps'], 'chunk_index': data['chunk_index'], 'decode_s': time.monotonic()-started, 'view_order': ['high', 'left', 'right'], 'device': a.device, 'dtype': 'float32', 'checkpoint': str(source_checkpoint), 'decoder_checkpoint': str(a.checkpoint)})
            path.with_suffix('.decode_error.json').unlink(missing_ok=True)
            count += 1
            print(f'Decoded {path} in {time.monotonic()-started:.2f}s', flush=True)
            combine(path.parent)
            if a.limit and count >= a.limit:
                break
        except Exception as exc:
            atomic_json(path.with_suffix('.decode_error.json'), {'error': repr(exc), 'at': time.time()})
            failed.add(path)
            print(f'Decode failed {path}: {exc}', flush=True)
        finally:
            vae.clear_cache()


if __name__ == '__main__':
    main()
