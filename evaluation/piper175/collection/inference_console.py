"""Web-owned inference lifecycle; delegates motion and recording to the existing client."""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode


class InferenceConsole:
    def inference_init(self):
        self.inference_process = None
        self.inference_cancelled = False
        self.inference_zero_cancelled = False
        self.inference_zero_process = None
        self.inference_result = None
        self.demo_cache_lock = threading.Lock()
        self.history_video_lock = threading.Lock()
        self.history_video_jobs = {}
        from web_console import ROOT
        state = ROOT/'.inference_last.json'
        try: self.inference_result = json.loads(state.read_text())
        except (OSError, ValueError): pass

    def inference_bridge(self, endpoint, body=None, binary=False):
        from web_console import ROOT
        config = json.loads((ROOT/'inference_bridge.json').read_text())
        request = Request(config['url']+endpoint, data=None if body is None else json.dumps(body).encode(),
                          headers={'X-Inference-Token':config['token'], 'Content-Type':'application/json'})
        try:
            timeout=600 if endpoint=='/history-video' else 120 if endpoint in ('/prepare','/demo-video') or endpoint.startswith('/demo-video?') else 10
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return raw if binary else json.loads(raw)
        except Exception as exc:
            if hasattr(exc, 'read'):
                try: raise RuntimeError(json.loads(exc.read()).get('error',str(exc))) from exc
                except (ValueError, AttributeError): pass
            raise RuntimeError('推理归档服务连接失败：'+str(exc)) from exc

    def save_inference_result(self):
        from web_console import ROOT
        path=ROOT/'.inference_last.json';tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.inference_result,ensure_ascii=False));tmp.replace(path)

    def inference_history(self):
        """Recent saved web runs, including runs that were never archived."""
        from web_console import ROOT
        base=ROOT.parent/'inference_client/inference_logs/web'
        rows=[]
        for parent in sorted(base.glob('[0-9]*'),reverse=True):
            if not parent.is_dir():continue
            try:request=json.loads((parent/'request.json').read_text())
            except (OSError,ValueError):continue
            selected=request.get('request') or {}
            for run in sorted(parent.glob('*'),reverse=True):
                if not run.is_dir() or not (run/'manifest.json').is_file():continue
                try:result=json.loads((run/'result.json').read_text())
                except (OSError,ValueError):result=None
                try:review=json.loads((run/'manual_evaluation.json').read_text())
                except (OSError,ValueError):review=None
                rows.append({'run':str(run.relative_to(base.parent)),
                             'video_id':selected.get('video_id'),
                             'profile':selected.get('profile'),
                             'mode':selected.get('mode'),
                             'prompt':selected.get('prompt') if selected.get('use_prompt',True) else '',
                             'use_prompt':selected.get('use_prompt',True),
                             'finished':result is not None,
                             'real_video_ready':(run/'combined.mp4').is_file(),
                             'prediction_video_ready':(run/'server_predictions/prediction_combined.mp4').is_file(),
                             'review':review if isinstance(review,dict) and (review.get('outcome') in ('success','failure') or review.get('note')) else None,
                             'result':{k:result.get(k) for k in ('completed','stopped_by_user','error','sent_steps','task_success')} if result else None})
        return rows

    def history_media_status(self, run):
        path = self.inference_run_path(run)
        with self.history_video_lock:
            job = dict(self.history_video_jobs.get(run) or {})
        return {'run':run,
                'real_ready':(path/'combined.browser.mp4').is_file(),
                'real_available':(path/'combined.mp4').is_file(),
                'prediction_ready':(path/'server_predictions/prediction_combined.mp4').is_file(),
                'sync':job}

    def history_video_path(self, run, kind):
        if kind not in ('real','prediction'):raise ValueError('视频类型无效')
        path=self.inference_run_path(run)
        video=path/'combined.browser.mp4' if kind=='real' else path/'server_predictions/prediction_combined.mp4'
        if not video.is_file():raise FileNotFoundError('该运行的视频尚未保存或预测尚未解码')
        return video

    def request_history_prediction(self, body):
        run=body.get('run')
        path=self.inference_run_path(run)
        with self.history_video_lock:
            old=self.history_video_jobs.get(run)
            if old and old.get('status')=='running':return self.history_media_status_unlocked(run,path)
            self.history_video_jobs[run]={'status':'running','message':'正在准备历史视频','updated_at':time.time()}
        def worker():
            errors=[]
            try:
                original=path/'combined.mp4';browser=path/'combined.browser.mp4'
                if original.is_file() and not browser.is_file():
                    import imageio_ffmpeg
                    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
                    temp=path/'combined.browser.partial.mp4'
                    subprocess.run([ffmpeg,'-nostdin','-y','-v','error','-i',str(original),
                                    '-c:v','libx264','-preset','veryfast','-crf','23',
                                    '-pix_fmt','yuv420p','-threads','2','-an',
                                    '-movflags','+faststart',str(temp)],check=True,timeout=600)
                    temp.replace(browser)
            except Exception as exc:
                errors.append('实拍视频处理失败：'+str(exc))
            try:
                if not (path/'server_predictions/prediction_combined.mp4').is_file():
                    rows=self.tail_records(path/'prediction_artifacts.jsonl')
                    sources={str(Path(row['latent_path']).parent) for row in rows if row.get('latent_path')}
                    if len(sources)!=1:raise ValueError('该次运行没有唯一的服务端预测记录')
                    request=json.loads((path.parent/'request.json').read_text()).get('request') or {}
                    profile=request.get('profile')
                    if profile not in ('29','r8000'):raise ValueError('该次运行没有可用的服务端配置')
                    with self.history_video_lock:self.history_video_jobs[run]['message']='正在从服务端同步预测视频'
                    self.inference_bridge('/history-video',{'run':run,'profile':profile,'trace_dir':sources.pop()})
            except Exception as exc:
                errors.append('预测视频同步失败：'+str(exc))
            status={'status':'failed' if errors else 'complete',
                    'message':'；'.join(errors) if errors else '实拍与预测视频已就绪',
                    'updated_at':time.time()}
            with self.history_video_lock:self.history_video_jobs[run]=status
        threading.Thread(target=worker,daemon=True).start()
        return self.history_media_status(run)

    def history_media_status_unlocked(self,run,path):
        return {'run':run,'real_ready':(path/'combined.browser.mp4').is_file(),
                'real_available':(path/'combined.mp4').is_file(),
                'prediction_ready':(path/'server_predictions/prediction_combined.mp4').is_file(),
                'sync':dict(self.history_video_jobs.get(run) or {})}

    def inference_review_stats(self):
        from web_console import ROOT
        base=ROOT.parent/'inference_client/inference_logs/web'
        counts={mode:{'success':0,'failure':0} for mode in ('with_prompt','no_prompt')}
        for p in base.glob('[0-9]*/*/manual_evaluation.json'):
            if not (p.parent/'result.json').is_file():continue
            try:
                if json.loads((p.parent/'manifest.json').read_text()).get('mode')!='execute':continue
                outcome=json.loads(p.read_text()).get('outcome')
                request=json.loads((p.parent.parent/'request.json').read_text()).get('request') or {}
            except (OSError,ValueError,AttributeError):continue
            mode='with_prompt' if request.get('use_prompt',True) else 'no_prompt'
            if outcome in ('success','failure'):counts[mode][outcome]+=1
        for row in counts.values():
            row['reviewed']=row['success']+row['failure']
            row['success_rate']=round(row['success']/row['reviewed']*100,1) if row['reviewed'] else None
        return counts

    def mark_inference_review(self, body):
        run=body.get('run');outcome=body.get('outcome')
        if not isinstance(run,str) or not run or outcome not in ('success','failure',None) or 'outcome' not in body:
            raise ValueError('运行或结果标记无效')
        note=body.get('note')
        if note is not None and (not isinstance(note,str) or len(note)>1000):
            raise ValueError('备注须为不超过 1000 字的文本')
        from web_console import ROOT
        base=(ROOT.parent/'inference_client/inference_logs/web').resolve()
        path=self.inference_run_path(run)
        path.relative_to(base)
        if not (path/'result.json').is_file():raise ValueError('推理尚未结束，不能标记')
        if json.loads((path/'manifest.json').read_text()).get('mode')!='execute':
            raise ValueError('仅真机执行运行计入成功率')
        label=path/'manual_evaluation.json'
        if note is None:
            try:note=json.loads(label.read_text()).get('note','')
            except (OSError,ValueError,AttributeError):note=''
        note=note.strip()
        if outcome is None and not note:
            label.unlink(missing_ok=True)
            return {'run':run,'review':None}
        review={'outcome':outcome,'note':note,'updated_at':time.time(),'source':'manual_console'}
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path,prefix='.manual_evaluation.',suffix='.tmp',delete=False) as stream:
            tmp=Path(stream.name)
            json.dump(review,stream,ensure_ascii=False,indent=2)
        try:os.replace(tmp,label)
        finally:tmp.unlink(missing_ok=True)
        return {'run':run,'review':review}

    def do_inference_archive(self, body):
        if not isinstance(body.get('target'),str) or not body['target'].strip():raise ValueError('请填写上传目录')
        run=body.get('run') or (self.inference_result or {}).get('run')
        path=self.inference_run_path(run or '')
        if not (path/'result.json').is_file():raise ValueError('请等待推理退出和实拍保存完成')
        request=json.loads((path.parent/'request.json').read_text())['request']
        job=self.inference_bridge('/archive', {'run':run,'profile':request['profile'],
                                               'target':body['target'].strip()})
        self.log('结果归档任务：'+job['id']+' · '+job['phase'])
        if self.inference_result and self.inference_result.get('run')==run:
            self.inference_result['archive']=job;self.save_inference_result()

    def start_prediction_preview(self, body):
        run=body.get('run');chunk=body.get('chunk')
        if type(chunk) is not int or not 0<=chunk<=100000:raise ValueError('chunk 编号无效')
        path=self.inference_run_path(run)
        request=json.loads((path.parent/'request.json').read_text())['request']
        rows=self.tail_records(path/'prediction_artifacts.jsonl')
        matches=[row for row in rows if row.get('chunk_index')==chunk]
        if len(matches)!=1 or not matches[0].get('video_path'):
            raise ValueError('该 chunk 尚无服务端预测记录')
        video=path/'server_predictions'/f'chunk_{chunk:05d}.mp4'
        if video.is_file():return {'run':run,'chunk':chunk,'status':'complete','phase':'视频已同步'}
        return self.inference_bridge('/preview',{'run':run,'chunk':chunk,
             'profile':request['profile'],'server_video':matches[0]['video_path']})


    def inference_catalog(self):
        from web_console import ROOT
        path = ROOT/'inference_demos.json'
        if not path.exists():path = ROOT.parent/'inference_client/zeroshot_oct21/validation_manifest.json'
        return json.loads(path.read_text()) if path.exists() else []

    def inference_demo_preview(self, identity):
        """Cache the browser-ready demo on the IPC; the broker handles 29 download/transcode."""
        from web_console import ROOT
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',identity) or identity not in {row['video_id'] for row in self.inference_catalog()}:
            raise ValueError('人类示教不存在')
        cache=ROOT.parent/'inference_client/demo_previews'
        cache.mkdir(parents=True,exist_ok=True)
        path=cache/(identity+'.mp4')
        with self.demo_cache_lock:
            if not path.is_file() or path.stat().st_size==0:
                raw=self.inference_bridge('/demo-video?'+urlencode({'id':identity}),binary=True)
                if not raw:raise RuntimeError('示教预览为空')
                temp=path.with_suffix('.partial.mp4')
                temp.write_bytes(raw)
                temp.replace(path)
        return path

    def validate_inference(self, body):
        if body.get('mode') not in ('check', 'shadow', 'execute'):
            raise ValueError('推理模式无效')
        if body.get('ready') is not True:
            raise ValueError('请确认示教布局和现场状态')
        if type(body.get('use_prompt',True)) is not bool:
            raise ValueError('文本 prompt 模式无效')
        if body.get('use_prompt',True) is False and not body.get('profile'):
            raise ValueError('无 prompt 模式须选择 29 或 R8000，以准备不含文本的示教编码')
        if type(body.get('max_chunks')) is not int or not 0 <= body['max_chunks'] <= 10000:
            raise ValueError('chunk 数须为 0–10000，0 表示连续执行')
        if not body.get('profile') and (type(body.get('port')) is not int or not 1 <= body['port'] <= 65535):
            raise ValueError('服务端隧道端口无效')
        if not isinstance(body.get('prompt'), str) or not body['prompt'].strip():
            raise ValueError('请填写本次示教的 prompt')
        rows = [r for r in self.inference_catalog() if r['video_id'] == body.get('video_id')]
        if len(rows) != 1:
            raise ValueError('请选择已配置的示教')
        if body.get('profile'):
            if body['profile'] not in ('29','r8000'):raise ValueError('请选择 29 或 R8000')
            if body['prompt'].strip()!=rows[0]['prompt'].strip():raise ValueError('当前编码示教须使用其对应 prompt')
        return rows[0]

    def do_inference_start(self, body):
        from web_console import ROOT
        from enable_zero import controllers
        row = self.validate_inference(body)
        endpoint = self.inference_bridge('/prepare',body) if body.get('profile') else {'host':'127.0.0.1','port':body['port'],'latent_path':row['latent_path']}
        runtime = ROOT.parent
        client = runtime/'inference_client'
        prompt_mode = 'with_prompt' if body.get('use_prompt',True) else 'no_prompt'
        output = client/'inference_logs/web'/(time.strftime('%Y%m%d_%H%M%S')+'_'+prompt_mode)
        output.mkdir(parents=True, exist_ok=False)
        logfile = output/'client.log'
        env = os.environ.copy()
        env.update(ZERO_WAM_ICL_LATENT_PATH=endpoint['latent_path'],
                   ZERO_WAM_VIDEO_GUIDANCE_SCALE='1' if body.get('use_prompt',True) else '-1',
                   ZERO_WAM_ICL_GUIDANCE_SCALE='5')
        env.pop('ZERO_WAM_ICL_ID', None)
        command = ['bash', str(client/'run.sh'), '--'+body['mode'], '--action-contract', 'piperpika',
                   '--prompt', body['prompt'].strip() if body.get('use_prompt',True) else '', '--server_host', endpoint['host'], '--server_port', str(endpoint['port']),
                   '--max_chunks', str(body['max_chunks']), '--save_dir', str(output), '--monitor-file', str(output/'monitor.json')]
        if body['mode'] != 'check':
            command.append('--record-continuous')
        (output/'request.json').write_text(json.dumps({'request':body, 'demo':row, 'command':command}, ensure_ascii=False, indent=2))
        with (runtime/'.recover_and_zero.lock').open('a') as motion_lock, (ROOT/'.relocalize.lock').open('a') as localization_lock:
            fcntl.flock(motion_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(localization_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if controllers():
                raise RuntimeError('另有机械臂控制程序，请先退出控制或推理')
            with logfile.open('w') as log:
                with self.lock:
                    if self.inference_cancelled:
                        return
                    # Explicit confirmed Start permits clearing only the software stop.
                    subprocess.run(['bash', str(client/'runtime_config.sh'), 'resume'], check=True)
                    try:
                        proc = subprocess.Popen(command, cwd=client, env=env, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, text=True, bufsize=1)
                    except BaseException:
                        subprocess.run(['bash', str(client/'runtime_config.sh'), 'stop'], check=True)
                        raise
                    self.inference_process = proc
                    self.inference_result = {'output':str(output), 'log':str(logfile), 'video_id':row['video_id'], 'mode':body['mode'], 'profile':body.get('profile'), 'use_prompt':body.get('use_prompt',True)}
                    self.save_inference_result()
                self.log('推理已启动：'+row['video_id']+'；日志：'+str(logfile))
                try:
                    for line in proc.stdout:
                        log.write(line);log.flush();self.log(line.rstrip())
                    code = proc.wait()
                finally:
                    with self.lock:
                        subprocess.run(['bash', str(client/'runtime_config.sh'), 'stop'], check=True)
                        self.inference_result['exit_code'] = proc.poll()
                        self.inference_process = None
                runs=[p for p in output.iterdir() if p.is_dir() and (p/'manifest.json').is_file()]
                if runs:
                    run=max(runs,key=lambda p:p.name);self.inference_last_run=run
                    self.inference_result['run']=str(run.relative_to(client/'inference_logs'))
                self.save_inference_result()
                if code and not self.inference_cancelled:
                    raise RuntimeError('推理客户端退出码 '+str(code)+'，请查看运行日志')

    def do_inference_stop(self, body):
        from web_console import ROOT
        auto_zero = body.get('auto_zero') is True
        with self.lock:
            self.inference_cancelled = True
            proc = self.inference_process
            if (proc and proc.poll() is None) or self.inference_view()['active_pids']:
                subprocess.run(['bash', str(ROOT.parent/'inference_client/runtime_config.sh'), 'stop'], check=True)
        self.log('已请求停止推理，等待客户端退出并保存视频；不发送下电或失能命令')
        if not auto_zero:
            return
        self.log('已安排停止后双臂归零；等待推理进程完全退出')
        deadline = time.monotonic() + 180
        while self.jobs.get('inference', {}).get('running') or self.inference_view()['active_pids']:
            if self.inference_zero_cancelled:
                raise RuntimeError('紧急停止已取消自动归零')
            if time.monotonic() >= deadline:
                raise RuntimeError('推理客户端 180 秒内未退出，已取消自动归零')
            time.sleep(.2)
        if self.inference_zero_cancelled:
            raise RuntimeError('紧急停止已取消自动归零')
        run = self.inference_result.get('run') if self.inference_result else None
        if not run or self.inference_result.get('mode') != 'execute':
            raise RuntimeError('没有已保存的真机推理结果，已取消自动归零')
        result_path = self.inference_run_path(run) / 'result.json'
        try:
            result = json.loads(result_path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError('推理结果未保存完整，已取消自动归零') from exc
        if not (result.get('stopped_by_user') and result.get('actuation_started')
                and result.get('software_stop_latched') and not result.get('error')
                and not result.get('stop_latch_error')):
            raise RuntimeError('推理未正常停止或存在执行故障，已取消自动归零')
        speed = body['speed_percent']
        self.inference_result['auto_zero'] = {'status': 'running', 'speed_percent': speed}
        self.save_inference_result()
        try:
            self.do_enable_zero({'arm': 'both', 'speed_percent': speed, 'auto_zero': True})
        except Exception as exc:
            self.inference_result['auto_zero'] = {'status': 'failed', 'speed_percent': speed, 'error': str(exc)}
            self.save_inference_result()
            raise
        self.inference_result['auto_zero'] = {'status': 'complete', 'speed_percent': speed}
        self.save_inference_result()
        self.log('停止后双臂归零完成；左臂、右臂均已确认到位')

    def inference_view(self):
        from web_console import ROOT
        root=(ROOT.parent/'inference_client/inference_logs').resolve()
        active=[]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                if entry.stat().st_uid!=os.getuid():continue
                args=(entry/'cmdline').read_bytes().decode().split('\0')
                if not any(Path(a).name=='real_robot_inference_merged.py' for a in args if a):continue
                cwd=(entry/'cwd').resolve()
                if cwd!=(ROOT.parent/'inference_client').resolve():continue
                base=Path(args[args.index('--save_dir')+1]);base=(cwd/base).resolve()
                base.relative_to(root)
                active.append(int(entry.name))
                candidates=[p for p in base.iterdir() if p.is_dir() and (p/'manifest.json').exists()]
                if candidates:self.inference_last_run=max(candidates,key=lambda p:p.name)
            except (OSError,ValueError,UnicodeError,IndexError):continue
        run=getattr(self,'inference_last_run',None)
        if not run and self.inference_result and self.inference_result.get('run'):
            try:run=self.inference_run_path(self.inference_result['run'])
            except (OSError,ValueError):pass
        if not run:return {'active_pids':active,'telemetry':None,'chunks':[]}
        data={'active_pids':active,'run':str(run.relative_to(root)),'output':str(run),'chunks':[]}
        monitor=run.parent/'monitor.json'
        try:
            telemetry=json.loads(monitor.read_text())
            if telemetry.get('output')!=str(run):telemetry=None
        except (OSError,ValueError):telemetry=None
        if telemetry:telemetry['age_s']=round(time.time()-telemetry['heartbeat'],2)
        data['telemetry']=telemetry
        data['feedback']=self.tail_records(run/'execution_feedback.jsonl')[-200:]
        data['timings']=self.tail_records(run/'chunk_timings.jsonl')[-20:]
        for row in self.tail_records(run/'prediction_artifacts.jsonl')[-100:]:
            index=int(row['chunk_index'])
            video=run/'server_predictions'/f'chunk_{index:05d}.mp4'
            data['chunks'].append({'index':index,'video_ready':video.is_file(),
                                   'server_video':row.get('video_path'),'has_prediction':(run/f'prediction_{index:04d}.npy').exists()})
        return data

    @staticmethod
    def tail_records(path):
        try:
            with path.open('rb') as f:
                f.seek(0,2);size=f.tell();f.seek(max(0,size-262144));raw=f.read()
            lines=raw.splitlines()
            if size>262144:lines=lines[1:]
            result=[]
            for line in lines:
                try:result.append(json.loads(line))
                except ValueError:continue
            return result
        except OSError:return []

    def inference_run_path(self, run):
        from web_console import ROOT
        root=(ROOT.parent/'inference_client/inference_logs').resolve()
        path=(root/run).resolve();path.relative_to(root)
        if not (path/'manifest.json').is_file():raise ValueError('推理运行不存在')
        return path

    def inference_prediction(self, run, index):
        import numpy as np
        path=self.inference_run_path(run)
        index=int(index)
        if not 0<=index<=100000:raise ValueError('chunk 编号无效')
        raw=np.load(path/f'prediction_{index:04d}.npy',allow_pickle=False)
        if raw.ndim!=3 or raw.shape[0]!=14 or raw.size>1000000 or not np.isfinite(raw).all():
            raise ValueError('预测轨迹格式无效')
        manifest=json.loads((path/'manifest.json').read_text())
        if not manifest.get('icl_latent_path'):raise ValueError('未确认示教契约')
        # These web runs use piperpika: left6, right6, left/right gripper.
        values=raw.reshape(14,-1).T
        values[:,:12]=np.rad2deg(values[:,:12]);values[:,12:]*=1000
        return {'index':index,'predicted':values.tolist(),
                'feedback':[r for r in self.tail_records(path/'execution_feedback.jsonl') if r.get('chunk')==index+1],
                'note':'原始预测顺序；前12通道为关节角度，末2通道为夹爪毫米。实测点按已执行步号显示，未宣称与原始采样一一同步。'}

    def do_inference_emergency(self, body):
        from web_console import ROOT
        import sys
        sys.path.insert(0,str(ROOT.parent/'inference_client'))
        from fault_stop import latch_software_stop
        # Independent of long-running job locks, disk conversion and Start requests.
        latch_software_stop(ROOT.parent/'inference_client/runtime_config.json')
        with self.lock:
            self.inference_cancelled=True
            self.inference_zero_cancelled=True
            zero_proc=self.inference_zero_process
            if zero_proc and zero_proc.poll() is None:
                try:os.killpg(zero_proc.pid, signal.SIGTERM)
                except ProcessLookupError:pass
        view=self.inference_view()
        self.log('紧急停止执行请求已发出：软件停止已锁定；不下电、不失能。已接收目标仍可能继续运动。')
        return {'stop_requested':True,'pids':view['active_pids']}
