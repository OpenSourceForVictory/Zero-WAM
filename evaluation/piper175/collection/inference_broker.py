"""Workstation-side demo preparation and durable archive jobs. SSH secrets stay here."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from archive_progress import stream_call, progress_fields
from urllib.parse import urlsplit, parse_qs

CAMERA_VIDEOS = ('cam_high.mp4', 'cam_left.mp4', 'cam_right.mp4')


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2)); tmp.replace(path)


def safe_run(value):
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
        raise ValueError('Invalid run path')
    return str(p)


def safe_target(value, root):
    p = PurePosixPath(value)
    if '..' in p.parts or not p.is_absolute(): raise ValueError('上传目录须为允许根目录下的绝对路径')
    p.relative_to(PurePosixPath(root))
    return str(p)


class Broker:
    def __init__(self, config):
        self.config = config; self.root = Path(config['state_dir']); self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock(); self.archive_lock = threading.Lock(); self.progress = threading.local()
        for path in self.root.glob('job_*.json'):
            job = json.loads(path.read_text())
            if job['status'] in ('queued', 'running'):
                job.update(status='failed', phase='服务重启，请重试归档', error='Archive service restarted')
                atomic_json(path, job)

    def call(self, argv, **kw):
        reporter=getattr(self.progress,'report',None)
        if reporter:return stream_call(argv,reporter,**kw)
        try:
            return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=kw.pop('timeout', 120), **kw).stdout
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('连接或处理超时，请检查对应机器网络及服务；可重试') from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError((exc.stderr or b'Command failed').decode(errors='replace')[-2000:]) from exc

    def ssh(self, host, command, **kw):
        return self.call(self.config['hosts'][host]['ssh']+[shlex.join(command)], **kw)

    def copy(self, host, source, dest, upload=False):
        cfg = self.config['hosts'][host]
        remote = cfg['address']+':'+source
        if upload: remote = cfg['address']+':'+dest; source, dest = source, remote
        else: source = remote
        self.call(['rsync', '-a', '--info=progress2', '--outbuf=L', '--protect-args', '--partial', '-e', shlex.join(cfg['ssh'][:-1]), source, dest], timeout=3600)

    def options(self):
        return {'profiles':[{k:p[k] for k in ('id','label','client_host','client_port')} for p in self.config['profiles'].values()],
                'default_target':self.config['default_target'], 'archive_root':self.config['archive_root']}

    def demo(self, identity):
        return next(d for d in self.config['demos'] if d['video_id'] == identity)

    def video(self, identity):
        demo = self.demo(identity)
        p = self.root/'demos'/(identity+'.mp4'); p.parent.mkdir(exist_ok=True)
        with self.lock:
            if not p.exists():
                tmp = p.with_suffix('.partial.mp4'); self.copy('29', demo['server_video'], str(tmp)); tmp.replace(p)
            preview=p.with_name(identity+'.browser.mp4')
            if not preview.exists():
                tmp=preview.with_suffix('.partial.mp4')
                ffmpeg=shutil.which('ffmpeg')
                if not ffmpeg:raise RuntimeError('示教预览需要 ffmpeg')
                self.call([ffmpeg,'-y','-nostdin','-v','error','-i',str(p),'-an','-c:v','libx264',
                           '-preset','fast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(tmp)],timeout=300)
                tmp.replace(preview)
        return preview

    def prepare(self, body):
        profile = self.config['profiles'][body['profile']]; demo = self.demo(body['video_id'])
        use_prompt = body.get('use_prompt', True)
        if type(use_prompt) is not bool:
            raise ValueError('文本 prompt 模式无效')
        if body.get('prompt', '').strip() != demo['prompt'].strip():
            raise ValueError('示教视频与 prompt 配对已更新，请刷新页面后重新核对布局')
        if body.get('auto_upload'):safe_target(body['upload_target'],self.config['archive_root'])
        self.ssh(profile['id'], ['python3','-c','import socket,sys;socket.create_connection(("127.0.0.1",int(sys.argv[1])),5).close()',str(profile['server_port'])])
        latent = demo['latent_path']
        if not use_prompt:
            source = PurePosixPath(latent)
            latent = str(source.parent/'no_prompt'/(source.stem+'_noprompt.pth'))
            script = '''import os,sys
from pathlib import Path
source,target=map(Path,sys.argv[1:3])
target.parent.mkdir(parents=True,exist_ok=True)
if not target.is_file():
    import torch
    data=torch.load(source,map_location="cpu",weights_only=False)
    if not isinstance(data,dict):raise TypeError("Demonstration latent must be a dictionary")
    data.pop("text_emb",None)
    data["prompt"]=""
    data["text_conditioning"]="empty_reset_prompt"
    temp=target.with_suffix(".partial.pth")
    torch.save(data,temp)
    os.replace(temp,target)
'''
            python = self.config['profiles']['29'].get('latent_python', '/mnt/sfs_turbo/public/apps/miniforge3/envs/lingbot-vggt/bin/python')
            self.ssh('29', [python,'-c',script,demo['latent_path'],latent], timeout=120)
        source_latent = latent
        if profile['id'] != '29':
            # A corrected demo gets a new source basename so neither the cache nor
            # the remote server can silently reuse an older prompt embedding.
            latent_name = PurePosixPath(latent).name
            if not re.fullmatch(r'[A-Za-z0-9_.-]+\.pth', latent_name):
                raise ValueError('Invalid latent filename')
            remote_latent = profile['demo_root']+'/'+latent_name
            cache = self.root/'latents'; cache.mkdir(exist_ok=True)
            p = cache/latent_name
            if not p.exists(): self.copy('29', source_latent, str(p))
            self.ssh(profile['id'], ['mkdir','-p',profile['demo_root']]); self.copy(profile['id'],str(p),remote_latent,True)
            latent = remote_latent
        # Socket probe only: never reset or consume a server session during preparation.
        self.ssh(profile['id'], ['python3','-c','import sys;from pathlib import Path;assert Path(sys.argv[1]).is_file()',latent])
        return {'host':profile['client_host'], 'port':profile['client_port'], 'latent_path':latent,
                'use_prompt':use_prompt}

    def jobs(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('job_*.json'),reverse=True)][:50]

    def enqueue(self, body):
        run = safe_run(body['run']); profile = body['profile']
        if profile not in self.config['profiles']: raise ValueError('Unknown server')
        target = safe_target(body['target'],self.config['archive_root'])
        with self.lock:
            for j in self.jobs():
                if (j['run'],j['profile'],j['target']) == (run,profile,target) and j['status'] in ('queued','running','complete'):
                    return j
            identity = time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
            job = dict(id=identity,run=run,profile=profile,target=target,status='queued',phase='等待归档',error=None,created_at=time.time(),events=[],step=0)
            atomic_json(self.root/('job_'+identity+'.json'),job)
        threading.Thread(target=self.archive,args=(job,),daemon=True).start()
        return job

    def archive(self, job):
        with self.archive_lock:
            self.archive_one(job)

    def decode_locally(self, profile, source, work):
        checkpoint=Path(profile['local_checkpoint'])
        weights=checkpoint/'vae/diffusion_pytorch_model.safetensors'
        digest=hashlib.sha256()
        with weights.open('rb') as stream:
            for block in iter(lambda:stream.read(16*1024*1024),b''):
                digest.update(block)
        if digest.hexdigest()!=profile['local_checkpoint_sha256']:
            raise ValueError('本机 4090 VAE 权重校验失败')
        trace_root=work/'gpu_trace';trace_root.mkdir(parents=True,exist_ok=True)
        run=trace_root/PurePosixPath(source).name;run.mkdir(exist_ok=True)
        host=self.config['hosts'][profile['id']]
        self.call(['rsync','-a','--info=progress2','--outbuf=L','--protect-args','--partial',
                   '-e',shlex.join(host['ssh'][:-1]),'--include=reset.json','--include=chunk_*.pt',
                   '--exclude=*',host['address']+':'+source+'/',str(run)+'/'],timeout=3600)
        reset=json.loads((run/'reset.json').read_text())
        source_checkpoint=reset['response']['model_path']
        local_out=work/'gpu_export'
        device=profile['decode_device']
        if device=='cuda:0':
            try:
                free_mib=int(subprocess.check_output(
                    ['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],
                    text=True,timeout=5).splitlines()[0].strip())
            except (OSError,ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired,IndexError) as exc:
                raise RuntimeError('无法检查本机 GPU 空闲显存，预测解码未启动') from exc
            # The FP32 three-view VAE exceeded 4.5 GiB free during a live archive.
            if free_mib < 8192:
                device='cpu'
                reporter=getattr(self.progress,'report',None)
                if reporter:reporter(f'本机 GPU 仅余 {free_mib} MiB，改用 CPU 解码预测视频')
        spec=dict(source=str(run),trace_root=str(trace_root),output=str(local_out),
                  code=str(Path(__file__).resolve().parents[3]),device=device,
                  decoder_script=profile['local_decoder_script'],
                  checkpoint_override=str(checkpoint),source_checkpoint=source_checkpoint,
                  reuse_decoded=True,threads=8 if device=='cpu' else 4)
        script=Path(__file__).with_name('inference_archive_remote.py').read_bytes()
        log=self.call([profile['local_python'],'-',json.dumps(spec)],input=script,timeout=7200)
        (work/'decode.log').write_bytes(log)
        return local_out

    def archive_one(self, job):
        path = self.root/('job_'+job['id']+'.json')
        job.setdefault('events',[]);job['started_at']=time.time()
        def report(line):
            now=time.time();job['updated_at']=now
            if line:
                try:job.update(progress_fields(line))
                except (ValueError,TypeError):pass
                job['last_activity_at']=now
                job['events']=(job['events']+[{'time':now,'text':line[-500:]}])[-30:]
            if now-job.get('_written',0)>.5:
                job['_written']=now;atomic_json(path,job)
        self.progress.report=report
        def stage(text,step):
            for key in ('done','total','unit','detail','transferred_bytes','transfer_percent','speed','transfer_time'):
                job.pop(key,None)
            job.update(status='running',phase=text,step=step,phase_started_at=time.time(),updated_at=time.time())
            job['events']=(job['events']+[{'time':time.time(),'text':text}])[-30:]
            atomic_json(path,job)
        try:
            stage('从工控机接收实拍和元数据',1)
            work = self.root/job['id']; incoming=work/'incoming'; incoming.mkdir(parents=True,exist_ok=True)
            run = self.config['ipc_log_root']+'/'+job['run']
            # Explicit allow-list: only videos and metadata from this exact run.
            cfg=self.config['hosts']['ipc']
            self.call(['rsync','-a','--info=progress2','--outbuf=L','--protect-args','--partial','-e',shlex.join(cfg['ssh'][:-1]),
                       '--include=combined.mp4',*[f'--include={name}' for name in CAMERA_VIDEOS],
                       '--include=*.json','--include=*.jsonl','--exclude=*',
                       cfg['address']+':'+run+'/',str(incoming)+'/'], timeout=3600)
            manifest=json.loads((incoming/'manifest.json').read_text())
            def demo_paths(d):
                source=PurePosixPath(d['latent_path'])
                plain=source.parent/'no_prompt'/(source.stem+'_noprompt.pth')
                paths={str(source),str(plain)}
                if job['profile']=='r8000':
                    root=self.config['profiles']['r8000']['demo_root']
                    paths.update((root+'/'+source.name,root+'/'+plain.name))
                return paths
            matches=[d for d in self.config['demos'] if manifest.get('icl_latent_path') in demo_paths(d)]
            atomic_json(incoming/'demo_selection.json',{'matches':matches,'prompt':manifest.get('prompt'),'server':job['profile']})
            result=json.loads((incoming/'result.json').read_text()) # Only finalized runs may be archived.
            real=incoming/'combined.mp4'
            if not real.is_file(): raise ValueError('本次没有已保存的实拍 combined.mp4，不能标为归档完成')
            missing=[name for name in CAMERA_VIDEOS if not (incoming/name).is_file()]
            if missing:raise ValueError('本次缺少三相机原始视频：'+', '.join(missing))
            stage('检查实拍视频与会话对应关系',2)
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:
                import imageio_ffmpeg
                ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
            self.call([ffmpeg,'-v','error','-i',str(real),'-f','null','-'],timeout=300)
            for name in CAMERA_VIDEOS:
                self.call([ffmpeg,'-v','error','-i',str(incoming/name),'-f','null','-'],timeout=300)
            rows=[json.loads(line) for line in (incoming/'prediction_artifacts.jsonl').read_text().splitlines() if line.strip()]
            sources={str(PurePosixPath(r['latent_path']).parent) for r in rows}
            if len(sources)!=1: raise ValueError('Prediction session is missing or ambiguous')
            profile=self.config['profiles'][job['profile']]; source=sources.pop()
            PurePosixPath(source).relative_to(profile['trace_root'])
            demo_id=matches[0]['video_id'] if len(matches)==1 else 'unregistered_demo'
            if not re.fullmatch(r'[A-Za-z0-9_-]+',demo_id):raise ValueError('Invalid demo identifier')
            job['demo_id']=demo_id
            prompt_mode='no_prompt' if manifest.get('prompt')=='' else 'with_prompt'
            job['use_prompt']=prompt_mode=='with_prompt'
            out=job['target']+'/'+job['profile']+'_'+demo_id+'_'+prompt_mode+'_'+PurePosixPath(job['run']).name
            job['output']=out
            stage('上传实拍与运行信息到 29',3)
            self.ssh('29',['mkdir','-p',out+'/real',out+'/metadata'])
            self.copy('29',str(real),out+'/real/combined.mp4',True)
            for name in CAMERA_VIDEOS:self.copy('29',str(incoming/name),out+'/real/'+name,True)
            for p in incoming.iterdir():
                if p.suffix in ('.json','.jsonl'):self.copy('29',str(p),out+'/metadata/'+p.name,True)
            stage('加载解码器并解码服务端预测视频',4)
            local_decode=profile['id']=='r8000' and profile.get('local_checkpoint')
            if local_decode:
                local_out=self.decode_locally(profile,source,work)
                stage('将本机预测结果上传到 29',5)
                self.copy('29',str(local_out/'predictions')+'/',out+'/predictions/',True)
                for name in ('prediction_verification.json','server_reset.json'):
                    self.copy('29',str(local_out/name),out+'/'+name,True)
            else:
                remote_out=out if profile['id']=='29' else profile['export_root']+'/'+job['id']
                spec=dict(source=source,trace_root=profile['trace_root'],output=remote_out,code=profile['code'],
                          device=profile['decode_device'],decoder_script=profile.get('decoder_script'),
                          env=profile.get('decode_env',{}))
                script=Path(__file__).with_name('inference_archive_remote.py').read_bytes()
                # Environment must be present before torch / torch_npu import.
                command=['env']+[k+'='+v for k,v in profile.get('decode_env',{}).items()]+[profile['python'],'-',json.dumps(spec)]
                log=self.ssh(profile['id'],command,input=script,timeout=7200)
                (work/'decode.log').write_bytes(log)
                if profile['id']!='29':
                    stage('将 R8000 预测结果上传到 29',5)
                    pred=work/'predictions';pred.mkdir()
                    self.copy(profile['id'],remote_out+'/predictions/',str(pred)+'/')
                    self.copy('29',str(pred)+'/',out+'/predictions/',True)
                    for name in ('prediction_verification.json','server_reset.json'):
                        self.copy(profile['id'],remote_out+'/'+name,str(work/name))
                        self.copy('29',str(work/name),out+'/'+name,True)
            stage('校验实拍、预测视频及文件完整性',6)
            verification=json.loads(self.ssh('29',['cat',out+'/prediction_verification.json']))
            prediction_sha=self.ssh('29',['sha256sum',out+'/predictions/prediction_combined.mp4']).decode().split()[0]
            if prediction_sha!=verification['prediction_sha256']:raise ValueError('Uploaded prediction checksum mismatch')
            if verification['prediction_chunks']<=max(int(r['chunk_index']) for r in rows):raise ValueError('Missing received prediction chunks')
            verification.update(status='complete',robot_run=job['run'],server=job['profile'],
                                real_bytes=real.stat().st_size,real_sha256=hashlib.sha256(real.read_bytes()).hexdigest(),
                                received_chunks=len(rows),stopped_by_user=result.get('stopped_by_user'),
                                client_error=result.get('error'),note='Final server prediction may not have been executed. Videos use their own frame rates, not wall-clock synchronization.')
            remote_sha=self.ssh('29',['sha256sum',out+'/real/combined.mp4']).decode().split()[0]
            if remote_sha!=verification['real_sha256']:raise ValueError('Uploaded real video checksum mismatch')
            camera_checks={}
            for name in CAMERA_VIDEOS:
                local_sha=hashlib.sha256((incoming/name).read_bytes()).hexdigest()
                remote_sha=self.ssh('29',['sha256sum',out+'/real/'+name]).decode().split()[0]
                if remote_sha!=local_sha:raise ValueError('Uploaded camera video checksum mismatch: '+name)
                camera_checks[name]={'bytes':(incoming/name).stat().st_size,'sha256':local_sha}
            verification['camera_videos']=camera_checks
            manifest=json.loads((incoming/'manifest.json').read_text())
            (work/'README.txt').write_text('Human latent: '+str(manifest.get('icl_latent_path'))+'\nPrompt: '+str(manifest.get('prompt'))+'\n'+verification['note']+'\n')
            (work/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>推理验证结果</h1><p>实拍和预测分别按自身帧率播放，不按墙钟同步。</p><h2>实拍</h2><p><a href="real/combined.mp4" download>三相机合成视频</a> · <a href="real/cam_high.mp4" download>顶部相机</a> · <a href="real/cam_left.mp4" download>左腕相机</a> · <a href="real/cam_right.mp4" download>右腕相机</a></p><h2>预测</h2><video controls width="960" src="predictions/prediction_combined.mp4"></video><p><a href="verification.json">校验与执行记录</a></p>')
            atomic_json(work/'verification.json',verification)
            stage('保存归档索引与校验记录',7)
            for name in ('README.txt','index.html','verification.json'):self.copy('29',str(work/name),out+'/'+name,True)
            job.update(status='complete',phase='上传与视频校验完成',verification=verification,error=None)
        except Exception as exc:
            detail=exc.stderr.decode(errors='replace')[-2000:] if isinstance(exc,subprocess.CalledProcessError) and exc.stderr else str(exc)
            job.update(status='failed',phase='归档失败，可重试；原始数据保留',error=detail)
        finally:
            self.progress.report=None;job['finished_at']=time.time();job['updated_at']=time.time();atomic_json(path,job)


def handler(broker):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def respond(self,code,value,mime='application/json'):
            data=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorized(self):
            import hmac
            return hmac.compare_digest(self.headers.get('X-Inference-Token',''),broker.config['token'])
        def do_GET(self):
            if not self.authorized():return self.respond(403,{'error':'Unauthorized'})
            try:
                path=urlsplit(self.path).path
                if path=='/options':return self.respond(200,broker.options())
                if path=='/demos':return self.respond(200,broker.config['demos'])
                if path=='/jobs':return self.respond(200,broker.jobs())
                if path=='/demo-video':return self.respond(200,broker.video(parse_qs(urlsplit(self.path).query)['id'][0]).read_bytes(),'video/mp4')
                return self.respond(404,{'error':'Not found'})
            except Exception as exc:return self.respond(503,{'error':str(exc)})
        def do_POST(self):
            if not self.authorized():return self.respond(403,{'error':'Unauthorized'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<16384:raise ValueError('Invalid request size')
                body=json.loads(self.rfile.read(size))
                if self.path=='/prepare':return self.respond(200,broker.prepare(body))
                if self.path=='/archive':return self.respond(202,broker.enqueue(body))
                return self.respond(404,{'error':'Not found'})
            except Exception as exc:return self.respond(409,{'error':str(exc)})
    return Handler


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    ThreadingHTTPServer((config['bind'],config['port']),handler(Broker(config))).serve_forever()
