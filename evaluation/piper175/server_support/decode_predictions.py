"""Decode atomically saved PiperPika chunks on a separate device; no model RPCs."""
import argparse
import json
from pathlib import Path
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, required=True)
    p.add_argument('--device', default='npu:0')
    p.add_argument('--once', action='store_true')
    a = p.parse_args()
    import types
    import numpy as np
    import torch
    import torch_npu
    import imageio.v2 as iio
    from wan_va import wan_va_server as server
    torch.npu.set_device(a.device)
    vae = server.load_vae(str(a.checkpoint / 'vae'), torch_dtype=torch.bfloat16, torch_device=a.device)
    obj = types.SimpleNamespace(vae=vae, video_processor=server.VideoProcessor(vae_scale_factor=1))
    failed = set()
    while True:
        for path in sorted(a.root.glob('piperpika_*/chunk_*.pt')):
            done = path.with_suffix('.decoded.json')
            if done.exists() or path in failed: continue
            started = time.monotonic()
            try:
                data = torch.load(path, map_location='cpu', weights_only=False)
                if Path(data['checkpoint']).resolve() != a.checkpoint.resolve():
                    raise ValueError('Checkpoint mismatch')
                latent = data['latent']
                if latent.ndim != 5 or latent.shape[-1] % 3:
                    raise ValueError(f'Unexpected three-view latent: {latent.shape}')
                vae.clear_cache()
                batch = torch.cat(latent.chunk(3, dim=-1), dim=0).to(device=a.device, dtype=vae.dtype)
                with torch.no_grad():
                    rgb = server.VA_Server.decode_one_video(obj, batch, 'np')
                rgb = np.rint(np.clip(rgb, 0, 1)*255).astype(np.uint8)
                vae.clear_cache()
                combined = np.concatenate(list(rgb), axis=2)
                tmp = path.with_suffix('.partial.mp4')
                iio.mimwrite(str(tmp), combined, fps=data['video_fps'], codec='libx264', macro_block_size=1, ffmpeg_params=['-crf','18'])
                tmp.replace(path.with_suffix('.mp4'))
                done.write_text(json.dumps({'frames':len(combined), 'shape':list(combined.shape),
                    'fps':data['video_fps'], 'chunk_index':data['chunk_index'],
                    'decode_s':time.monotonic()-started, 'view_order':['high','left','right']}, indent=2))
                print(f'Decoded {path}', flush=True)
            except Exception as exc:
                path.with_suffix('.decode_error.json').write_text(json.dumps({'error':repr(exc)}))
                failed.add(path)
                print(f'Decode failed {path}: {exc}', flush=True)
        if a.once: break
        time.sleep(2)


if __name__ == '__main__': main()
