"""Decode completed bread_eval traces into three-view prediction videos."""
import argparse
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--checkpoint',type=Path,required=True)
    parser.add_argument('--device',required=True)
    args=parser.parse_args();p=args.run.resolve()
    if not json.loads((p/'completion.json').read_text())['success']:
        raise ValueError('Inference run did not complete')
    if __package__:
        from .bread_eval import digest
    else:
        from bread_eval import digest
    for row in json.loads((p/'checkpoint_manifest.json').read_text()):
        if row['path'].startswith('vae/') and digest(args.checkpoint/row['path']) != row['sha256']:
            raise ValueError('VAE differs from the inference checkpoint')
    import types
    import numpy as np
    import torch
    import imageio.v2 as iio
    from PIL import Image, ImageDraw
    if args.device.startswith('npu'):
        import torch_npu  # noqa: F401
        torch.npu.set_device(args.device)
    elif args.device.startswith('cuda'):
        torch.cuda.set_device(args.device)
    else:
        raise ValueError('Expected cuda:N or npu:N')
    from wan_va import wan_va_server as server
    vae=server.load_vae(str(args.checkpoint/'vae'),torch_dtype=torch.bfloat16,torch_device=args.device)
    obj=types.SimpleNamespace(vae=vae,video_processor=server.VideoProcessor(vae_scale_factor=1))
    def decode(latent):
        vae.clear_cache()
        batch=torch.cat(latent.chunk(3,dim=-1),dim=0).to(device=args.device,dtype=vae.dtype)
        with torch.no_grad():rgb=server.VA_Server.decode_one_video(obj,batch,'np')
        vae.clear_cache()
        return np.rint(np.clip(rgb,0,1)*255).astype(np.uint8)
    rows=[]
    for result in json.loads((p/'results.json').read_text()):
        d=p/'runs'/result['case'];cfg=json.loads((d/'config.json').read_text())
        latent=torch.load(d/'final_latents.pt',map_location='cpu',weights_only=False)
        rgb=decode(latent['predicted']);initial=decode(latent['initial'])
        pre=torch.load(d/'observation_preprocessed.pt',map_location='cpu',weights_only=False)
        actual=np.rint(np.clip((pre.float().numpy()[:,:,0].transpose(0,2,3,1)+1)*127.5,0,255)).astype(np.uint8)
        fps=json.loads((p/'settings.json').read_text())['contract']['model_video_fps']
        # Decoded frames follow the model video timebase, not the action sampling rate.
        iio.mimwrite(str(d/'predicted_three_views.mp4'),np.concatenate(list(rgb),axis=2),fps=fps,codec='libx264',macro_block_size=1)
        sampled=d/'demo_sampled_rgb_uint8.npy'
        iio.mimwrite(str(d/'demo_model_input.mp4'),np.load(sampled),fps=cfg['icl_fps'],codec='libx264',macro_block_size=1)
        w,h=rgb.shape[3],rgb.shape[2];canvas=Image.new('RGB',(3*w,3*(h+24)),'white');draw=ImageDraw.Draw(canvas)
        for i,(label,images) in enumerate([('Actual observation',actual),('VAE reconstructed observation',initial[:,0]),('Generated last frame',rgb[:,-1])]):
            draw.text((4,i*(h+24)+4),label,fill='black')
            for j,im in enumerate(images):canvas.paste(Image.fromarray(im),(j*w,i*(h+24)+24))
        canvas.save(d/'observation_vs_prediction.jpg',quality=95)
        rows.append(dict(case=result['case'],video_frames=rgb.shape[1],video_fps=fps,robot_commands=0))
    (p/'decode_completion.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
