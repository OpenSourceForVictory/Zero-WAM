"""Web-owned inference lifecycle; delegates motion and recording to the existing client."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import time


class InferenceConsole:
    def inference_init(self):
        self.inference_process = None
        self.inference_cancelled = False
        self.inference_result = None

    def inference_catalog(self):
        from web_console import ROOT
        path = ROOT.parent/'inference_client/zeroshot_oct21/validation_manifest.json'
        return json.loads(path.read_text()) if path.exists() else []

    def validate_inference(self, body):
        if body.get('mode') not in ('check', 'shadow', 'execute'):
            raise ValueError('推理模式无效')
        if body.get('ready') is not True:
            raise ValueError('请确认示教布局、prompt 和现场状态')
        if type(body.get('max_chunks')) is not int or not 0 <= body['max_chunks'] <= 10000:
            raise ValueError('chunk 数须为 0–10000，0 表示连续执行')
        if type(body.get('port')) is not int or not 1 <= body['port'] <= 65535:
            raise ValueError('服务端隧道端口无效')
        if not isinstance(body.get('prompt'), str) or not body['prompt'].strip():
            raise ValueError('请填写本次示教的 prompt')
        rows = [r for r in self.inference_catalog() if r['video_id'] == body.get('video_id')]
        if len(rows) != 1:
            raise ValueError('请选择已配置的示教')
        return rows[0]

    def do_inference_start(self, body):
        from web_console import ROOT
        from enable_zero import controllers
        row = self.validate_inference(body)
        runtime = ROOT.parent
        client = runtime/'inference_client'
        output = client/'inference_logs/web'/time.strftime('%Y%m%d_%H%M%S')
        output.mkdir(parents=True, exist_ok=False)
        logfile = output/'client.log'
        env = os.environ.copy()
        env.update(ZERO_WAM_ICL_LATENT_PATH=row['latent_path'],
                   ZERO_WAM_VIDEO_GUIDANCE_SCALE='1', ZERO_WAM_ICL_GUIDANCE_SCALE='5')
        env.pop('ZERO_WAM_ICL_ID', None)
        command = ['bash', str(client/'run.sh'), '--'+body['mode'], '--action-contract', 'piperpika',
                   '--prompt', body['prompt'].strip(), '--server_port', str(body['port']),
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
                    self.inference_result = {'output':str(output), 'log':str(logfile), 'video_id':row['video_id'], 'mode':body['mode']}
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
                if code and not self.inference_cancelled:
                    raise RuntimeError('推理客户端退出码 '+str(code)+'，请查看运行日志')

    def do_inference_stop(self, body):
        from web_console import ROOT
        with self.lock:
            self.inference_cancelled = True
            proc = self.inference_process
            if proc and proc.poll() is None:
                subprocess.run(['bash', str(ROOT.parent/'inference_client/runtime_config.sh'), 'stop'], check=True)
                proc.send_signal(signal.SIGINT)
        self.log('已请求停止推理，等待客户端退出并保存视频；不发送下电或失能命令')

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
        self.inference_cancelled=True
        view=self.inference_view()
        for pid in view['active_pids']:
            try:os.kill(pid,signal.SIGINT)
            except ProcessLookupError:pass
        self.log('紧急停止执行请求已发出：软件停止已锁定；不下电、不失能。已接收目标仍可能继续运动。')
        return {'stop_requested':True,'pids':view['active_pids']}
