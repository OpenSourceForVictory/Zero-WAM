"""Held-out bread evaluation. No robot SDK, CAN, enable or motion commands.

prepare: inspect all videos and copy only held-out demos into a portable bundle.
run: recorded three-view observations -> traced, one-chunk inference per seed.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

CAMERAS = tuple('observation.images.' + k for k in
                ('cam_high', 'cam_left_wrist', 'cam_right_wrist'))
SEEDS = [42, 123, 456, 789, 2026]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def video_split(name):
    if re.fullmatch(r'bread_test\d+\.mp4', name):
        return 'test'
    if re.fullmatch(r'bread_\d+\.mp4', name):
        return 'train'
    return None


def prepare(source, output):
    import cv2
    source, output = Path(source).resolve(), Path(output).resolve()
    videos = []
    for path in sorted(source.iterdir()):
        split = video_split(path.name)
        if not split or not path.is_file():
            continue
        cap = cv2.VideoCapture(str(path))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            declared = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            count, shape, first = 0, None, None
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if first is None:
                    first, shape = frame.copy(), list(frame.shape)
                if list(frame.shape) != shape:
                    raise ValueError(f'Changing resolution: {path}')
                count += 1
            if count == 0 or fps <= 0 or not math.isfinite(fps) or (declared > 0 and count != declared):
                raise ValueError(f'Incomplete/invalid video: {path}, decoded={count}, declared={declared}')
        finally:
            cap.release()
        videos.append(dict(id=path.stem, split=split, source=str(path), sha256=digest(path),
                           bytes=path.stat().st_size, fps=fps, frames=count,
                           duration_s=count/fps, shape=shape))
    train = [v for v in videos if v['split'] == 'train']
    test = [v for v in videos if v['split'] == 'test']
    if not train or not test:
        raise ValueError('Both training and held-out videos are required')
    if {v['sha256'] for v in train} & {v['sha256'] for v in test}:
        raise ValueError('Exact duplicate video content across train/test split')
    output.mkdir(parents=True, exist_ok=False)
    (output/'videos').mkdir()
    for v in test:
        v['bundle_path'] = 'videos/' + Path(v['source']).name
        shutil.copy2(v['source'], output/v['bundle_path'])
        assert digest(output/v['bundle_path']) == v['sha256']
    manifest = dict(schema_version=1, videos=videos, default_seeds=SEEDS,
                    split_authority='User-designated filenames; not proof of training-pipeline exclusion',
                    note='Only held-out videos are copied. Near-duplicate content is not automatically detected.')
    save(output/'manifest.json', manifest)
    save(output/'training_contract.template.json', dict(
        action_convention='episode_relative_physical_xyzw',
        model_action_dt_s=None, model_actions_per_latent=None, model_video_fps=None,
        stats_path=None, stats_sha256=None,
        icl_fps=None, icl_height=None, icl_width=None, icl_rope_h=None, icl_guidance_scale=None,
        demo_preprocessing=None,
        note='Fill using the completed training job; no implicit Robotwin/ALOHA statistics fallback.'))
    save(output/'trial_record.template.json', dict(
        checkpoint_sha256=None, demo_id=None, seed=None, initial_capture=None,
        trial_id=None, attempt=None, task_instruction=None, success_criterion=None,
        success=None, failure_reason=None, intervention=None,
        robot_log=None, robot_video=None, completed_chunks=None,
        note='Offline displacement is not task success. Record every attempted physical trial, including aborts.'))
    return manifest


def validate_bundle(bundle):
    bundle = Path(bundle).resolve()
    m = json.loads((bundle/'manifest.json').read_text())
    videos = m['videos']
    if not videos or any(video_split(Path(v['source']).name) != v['split'] for v in videos):
        raise ValueError('Invalid video split manifest')
    if len({v['id'] for v in videos}) != len(videos):
        raise ValueError('Duplicate demo IDs')
    if {v['sha256'] for v in videos if v['split']=='train'} & {v['sha256'] for v in videos if v['split']=='test'}:
        raise ValueError('Train/test content overlap')
    tests = [v for v in videos if v['split']=='test']
    if not tests:
        raise ValueError('No held-out test demos')
    for v in tests:
        path = (bundle/v['bundle_path']).resolve()
        if not path.is_relative_to(bundle) or digest(path) != v['sha256']:
            raise ValueError('Missing, modified or out-of-bundle test video')
    return m, tests


def load_contract(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text())
    if c.get('action_convention') != 'episode_relative_physical_xyzw':
        raise ValueError('This evaluator requires episode-relative physical XYZ / xyzw / absolute gripper')
    count, dt, fps = (c.get(k) for k in ('model_actions_per_latent','model_action_dt_s','model_video_fps'))
    if type(count) is not int or count < 1 or any(type(x) not in (int,float) or not math.isfinite(x) or x <= 0 for x in (dt,fps)):
        raise ValueError('Explicit positive training timing is required')
    if not math.isclose(count*dt, 4/fps, rel_tol=1e-5):
        raise ValueError('Training timing mismatch: count * action_dt != 4 / video_fps')
    if not c.get('stats_path') or not c.get('stats_sha256'):
        raise ValueError('Explicit training statistics path and SHA256 required')
    stats = (path.parent/c['stats_path']).resolve()
    if digest(stats) != c['stats_sha256']:
        raise ValueError('Training statistics SHA256 mismatch')
    for key in ('icl_fps','icl_height','icl_width','icl_rope_h'):
        if type(c.get(key)) is not int or c[key] <= 0:
            raise ValueError(f'Explicit positive integer {key} required')
    if type(c.get('icl_guidance_scale')) not in (int,float) or not math.isfinite(c['icl_guidance_scale']) or c['icl_guidance_scale'] <= 1:
        raise ValueError('This ICL evaluator requires explicit guidance scale > 1')
    if c.get('demo_preprocessing') != 'server_native_bilinear_no_extra_rotation':
        raise ValueError('Confirm native server preprocessing matches training; other crop/rotation pipelines need an explicit adapter')
    return c, stats


def observation(path):
    import numpy as np
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != set(CAMERAS):
            raise ValueError('Expected exactly head, left wrist, right wrist RGB arrays')
        out = {k:data[k].copy() for k in CAMERAS}
    if any(v.dtype != np.uint8 or v.ndim != 3 or v.shape[2] != 3 or min(v.shape[:2]) < 2 for v in out.values()):
        raise ValueError('Expected HxWx3 uint8 RGB images')
    return out


def action_metrics(raw, count):
    import numpy as np
    raw = np.asarray(raw)
    if raw.shape != (16,2,count) or not np.isfinite(raw).all():
        raise ValueError(f'Invalid action tensor: {raw.shape}')
    a = raw[:,1,:].T
    norms = np.stack([np.linalg.norm(a[:,j:j+3],axis=1)*100 for j in (0,8)],axis=1)
    return dict(first_cm=norms[0].tolist(), max_chunk_cm=norms.max(0).tolist(),
                first_xyz_m=[a[0,:3].tolist(),a[0,8:11].tolist()],
                quaternion_norm_range=[[float(np.linalg.norm(a[:,j:j+4],axis=1).min()),float(np.linalg.norm(a[:,j:j+4],axis=1).max())] for j in (3,11)],
                task_success=None, robot_commands=0)


def run(args):
    import copy
    import importlib
    import numpy as np
    manifest, demos = validate_bundle(args.bundle)
    contract, stats = load_contract(args.contract)
    serving = args.command == 'serve'
    obs = None if serving else observation(args.observation)
    if not args.prompt.strip():
        raise ValueError('A nonempty task description is required by the ICL server')
    if len(set(args.seeds)) != len(args.seeds) or any(s < 0 or s >= 2**32 for s in args.seeds):
        raise ValueError('Seeds must be unique uint32 values')
    checkpoint = Path(args.checkpoint).resolve()
    for name in ('transformer','vae','text_encoder','tokenizer'):
        if not (checkpoint/name).is_dir():
            raise ValueError(f'Expected exported inference checkpoint: missing {name}; merge/export adapter-only weights first')
    output = Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    if not serving:shutil.copy2(args.observation, output/'observation.npz')
    shutil.copy2(stats, output/'action_stats.json')
    save(output/'split_manifest.json',manifest)
    # Hash weight files once per invocation so a renamed/replaced checkpoint is auditable.
    weights = [{'path':str(f.relative_to(checkpoint)), 'bytes':f.stat().st_size,'sha256':digest(f)}
               for f in sorted(checkpoint.rglob('*')) if f.is_file() and '.cache' not in f.parts]
    save(output/'checkpoint_manifest.json',weights)
    settings = dict(checkpoint=str(checkpoint), checkpoint_sha256=hashlib.sha256(json.dumps(weights,sort_keys=True).encode()).hexdigest(),
                    contract=contract, observation_sha256=None if serving else digest(args.observation),
                    demo_sha256={d['id']:d['sha256'] for d in demos}, seeds=args.seeds,
                    demo_descriptions={d['id']:d.get('task_description',args.prompt) for d in demos},
                    icl_fps=contract['icl_fps'], icl_guidance_scale=contract['icl_guidance_scale'], icl_rope_h=contract['icl_rope_h'],
                    target_text='empty', task_description=args.prompt, robot_commands=0)
    save(output/'settings.json',settings)
    import torch
    if args.device.startswith('npu'):
        import torch_npu  # noqa: F401
        torch.npu.set_device(args.device)
    elif args.device.startswith('cuda'):
        torch.cuda.set_device(args.device)
    else:
        raise ValueError('Specify cuda:N or npu:N')
    from wan_va.configs.va_robotwin_cfg import va_robotwin_cfg, load_robotwin_norm_stat
    from wan_va import wan_va_server as server
    os.environ['ZERO_WAM_TRACE_ROOT']=str(output/'runs')
    import importlib.util
    spec=importlib.util.spec_from_file_location('bread_trace',Path(__file__).with_name('aloha_trace.py'))
    trace=importlib.util.module_from_spec(spec);spec.loader.exec_module(trace)
    c=copy.deepcopy(va_robotwin_cfg)
    c.device=args.device;c.local_rank=0;c.rank=0;c.world_size=1;c.enable_offload=False
    c.model_path=str(checkpoint);c.save_root=str(output)
    c.norm_stat=load_robotwin_norm_stat(stats);c.action_stats_path=str(stats)
    c.action_per_frame=contract['model_actions_per_latent']
    c.guidance_scale=-1
    for key in ('icl_guidance_scale','icl_rope_h','icl_fps','icl_height','icl_width'):c[key]=contract[key]
    c.num_inference_steps=50;c.action_num_inference_steps=50
    settings['inference_config']=json.loads(json.dumps({k:v for k,v in dict(c).items() if k not in ('model_path','save_root','norm_stat','action_stats_path')},default=str))
    settings['server_sha256']=digest(server.__file__)
    settings['trace_sha256']=digest(Path(__file__).with_name('aloha_trace.py'))
    save(output/'settings.json',settings)
    save(output/'server_source.json',dict(path=server.__file__,sha256=digest(server.__file__)))
    server.init_logger();torch.set_num_threads(8);model=server.VA_Server(c);rows=[]
    if serving:
        from wan_va.utils.Simple_Remote_Infer.deploy.registered_policy import RegisteredPolicy
        from wan_va.utils.Simple_Remote_Infer.deploy.websocket_policy_server import WebsocketPolicyServer
        import uuid
        save(output/'registry.json',{d['id']:{'video':str((Path(args.bundle)/d['bundle_path']).resolve())} for d in demos})
        registered=RegisteredPolicy(model,output/'registry.json')
        class RecordedPolicy:
            first = False
            def infer(self, request):
                request=dict(request)
                if request.get('reset'):
                    request.update(trace_name='trial_'+uuid.uuid4().hex,
                                   video_guidance_scale=-1,icl_guidance_scale=contract['icl_guidance_scale'])
                    request['prompt']=settings['demo_descriptions'].get(request.get('icl_id'),args.prompt)
                    self.first=True
                elif self.first and 'obs' in request and not request.get('compute_kv_cache'):
                    request['diagnostic_seed']=args.seeds[0];self.first=False
                return registered.infer(request)
        metadata=dict(protocol_version=1,executed_action_history=True,icl_ids=[d['id'] for d in demos],
                      model=str(checkpoint),checkpoint_sha256=settings['checkpoint_sha256'],
                      training_contract=contract,seed=args.seeds[0],deployment_validated=False,
                      trace_scope='First inference chunk per reset; client must record later execution/observations')
        save(output/'server_metadata.json',metadata)
        WebsocketPolicyServer(RecordedPolicy(),host=args.host,port=args.port,metadata=metadata,single_client=True).serve_forever()
        return
    try:
        for demo in demos:
            for seed in args.seeds:
                case=f"{demo['id']}_seed{seed}"
                model.infer(dict(reset=True,use_icl=True,prompt=demo.get('task_description',args.prompt),trace_name=case,
                                 icl_video_path=str((Path(args.bundle)/demo['bundle_path']).resolve()),
                                 video_guidance_scale=-1,icl_guidance_scale=contract['icl_guidance_scale']))
                raw=model.infer(dict(obs=obs,diagnostic_seed=seed))['action']
                metrics=action_metrics(raw,c.action_per_frame)
                np.save(output/'runs'/case/'returned_action.npy',raw)
                np.savetxt(output/'runs'/case/'valid_actions.csv',np.asarray(raw)[:,1,:].T,delimiter=',')
                rows.append(dict(case=case,demo=demo['id'],seed=seed,**metrics))
                save(output/'results.json',rows);print(json.dumps(rows[-1]),flush=True)
        save(output/'completion.json',dict(success=True,runs=len(rows),robot_commands=0))
    except BaseException as exc:
        save(output/'completion.json',dict(success=False,completed_runs=len(rows),error=repr(exc),robot_commands=0))
        raise


def compare(before, after, output):
    before, after = Path(before), Path(after)
    a,b=[json.loads((p/'settings.json').read_text()) for p in (before,after)]
    for key in ('observation_sha256','demo_sha256','demo_descriptions','seeds','icl_fps','icl_guidance_scale','icl_rope_h','target_text','inference_config','server_sha256','trace_sha256'):
        if a[key] != b[key]:
            raise ValueError(f'Unmatched comparison: {key}')
    for key in ('action_convention','model_action_dt_s','model_actions_per_latent','model_video_fps','icl_height','icl_width','demo_preprocessing'):
        if a['contract'][key] != b['contract'][key]:
            raise ValueError(f'Incompatible action contract: {key}')
    runs=[]
    for p in (before,after):
        if not json.loads((p/'completion.json').read_text())['success']:
            raise ValueError('Cannot compare incomplete runs')
        runs.append({(r['demo'],r['seed']):r for r in json.loads((p/'results.json').read_text())})
    expected={(d,s) for d in a['demo_sha256'] for s in a['seeds']}
    if any(set(r)!=expected for r in runs):
        raise ValueError('Missing comparison cases')
    report=dict(before_checkpoint=a['checkpoint_sha256'],after_checkpoint=b['checkpoint_sha256'],
                statistics_changed=a['contract']['stats_sha256']!=b['contract']['stats_sha256'],
                note='Paired offline first-action comparison, not task success or authorization to execute.',
                cases=[dict(demo=d,seed=s,before_cm=runs[0][d,s]['first_cm'],after_cm=runs[1][d,s]['first_cm']) for d,s in sorted(expected)])
    with Path(output).open('x') as f:json.dump(report,f,indent=2)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--source',type=Path,required=True);prep.add_argument('--output',type=Path,required=True)
    check=sub.add_parser('check');check.add_argument('--bundle',type=Path,required=True);check.add_argument('--contract',type=Path)
    for name in ('run','serve'):
        runp=sub.add_parser(name)
        for k in ('bundle','contract','checkpoint','output'):runp.add_argument('--'+k,type=Path,required=True)
        if name=='run':runp.add_argument('--observation',type=Path,required=True)
        else:
            runp.add_argument('--host',default='127.0.0.1');runp.add_argument('--port',type=int,default=29059)
        runp.add_argument('--device',required=True);runp.add_argument('--seeds',nargs='+',type=int,default=SEEDS if name=='run' else [42])
        runp.add_argument('--prompt',required=True,help='Recorded task description; actual target text remains empty under ICL CFG')
    comp=sub.add_parser('compare')
    for k in ('before','after','output'):comp.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':print(json.dumps(prepare(a.source,a.output),indent=2))
    elif a.command=='check':
        print(json.dumps(validate_bundle(a.bundle)[1],indent=2))
        if a.contract:print(json.dumps(load_contract(a.contract)[0],indent=2))
    elif a.command in ('run','serve'):
        if a.command=='serve' and len(a.seeds)!=1:p.error('serve requires exactly one seed per service run')
        run(a)
    else:print(json.dumps(compare(a.before,a.after,a.output),indent=2))


if __name__=='__main__':main()
