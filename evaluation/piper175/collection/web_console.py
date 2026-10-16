"""LAN collection console. Reuses validated controller/recorder/export scripts."""
import argparse
from collections import deque
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent
CAMERAS={'left':'CC1C810002Y','high':'CC1C8100041','right':'CC1C8100015'}


def rpc(command, timeout=45):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout);sock.connect(str(ROOT/'.switch.sock'))
        sock.sendall((command+'\n').encode());parts=[]
        while True:
            part=sock.recv(65536)
            if not part:break
            parts.append(part)
    result=json.loads(b''.join(parts))
    if result.get('command_error'):raise RuntimeError(result['command_error'])
    return result


def task_path(root,name):
    if not isinstance(name,str) or not re.fullmatch(r'[\w\-\u4e00-\u9fff]{1,80}',name):
        raise ValueError('任务名须为 1–80 个汉字、字母、数字、下划线或短横线')
    path=(root/name).resolve()
    if path.parent!=root.resolve():raise ValueError('任务路径无效')
    return path


class Console:
    def __init__(self,data_root):
        self.root=Path(data_root).expanduser().resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.logs=deque(maxlen=100);self.lock=threading.RLock();self.rpc_lock=threading.RLock();self.jobs={};self.controller=None
        self.record=None;self.record_lock=None;self.last_result=None;self.collection_session=None
        self.config={"task":"pika_dual_demo","prompt":"","max_frames":5000,"fps":30}
        config_file=self.root/".console_config.json"
        if config_file.exists():self.config.update(json.loads(config_file.read_text()))
        self.control={'connected':False};self.closed=threading.Event();self.closing=False;self.workers=[]
        self.poller=threading.Thread(target=self.poll,daemon=True);self.poller.start()

    def log(self,text):
        with self.lock:self.logs.append({'time':time.strftime('%H:%M:%S'),'text':str(text)})

    def call(self,command,timeout=45):
        with self.rpc_lock:return rpc(command,timeout)

    def poll(self):
        while not self.closed.is_set():
            if not self.rpc_lock.acquire(blocking=False):
                self.closed.wait(.2);continue
            try:control={'connected':True,**self.call('status',1.5)}
            except Exception as exc:
                # Do not present an old active state as a current observation.
                control={'connected':False,'message':str(exc)}
            finally:self.rpc_lock.release()
            with self.lock:self.control=control
            self.closed.wait(1)

    def snapshot(self):
        with self.lock:
            rec=self.record
            recording=None if not rec else {k:rec.get(k) for k in ('id','task','phase','frames','started_at','episode','error')}
            if recording:recording['elapsed_s']=round(time.time()-rec['started_at'],1)
            return {'control':dict(self.control),'recording':recording,'jobs':dict(self.jobs),
                    'logs':list(self.logs),'last_result':self.last_result,'data_root':str(self.root),
                    'conversion':'legacy28','fps':(self.collection_session or self.config)['fps'],'session':self.collection_session,'config':dict(self.config)}

    def submit(self,action,body):
        groups={'controller_start':'control','left':'control','right':'control','pause':'control','controller_stop':'control',
                'session_start':'record','config_save':'record','start':'record','save':'record','discard':'record','exit':'record','delete':'record','delete_last':'record','convert':'convert'}
        if action not in groups:raise ValueError('未知操作')
        group=groups[action]
        with self.lock:
            if self.closing:raise ValueError('控制台正在退出')
            if self.jobs.get(group,{}).get('running'):raise ValueError('该操作正在处理，请稍候')
            if group in ('record','convert') and self.jobs.get('convert',{}).get('running'):
                raise ValueError('正在转换，请完成后再操作录制数据')
            if action=='convert' and (self.collection_session or self.record or self.jobs.get('record',{}).get('running')):
                raise ValueError('请先结束录制再转换')
            if action=='controller_start' and body.get('ready') is not True:
                raise ValueError('请先确认现场已准备好')
            if action in ('save','discard','exit') and self.record and body.get('recording_id')!=self.record['id']:
                raise ValueError('录制状态已变化，请刷新后重试')
            self.jobs[group]={'running':True,'action':action,'error':None}
        def work():
            try:
                getattr(self,'do_'+action)(body)
                self.log('完成：'+action)
            except Exception as exc:
                self.log('失败：'+action+' · '+str(exc))
                with self.lock:self.jobs[group]['error']=str(exc)
            finally:
                with self.lock:self.jobs[group]['running']=False
        worker=threading.Thread(target=work,daemon=True)
        with self.lock:
            self.workers=[(g,t) for g,t in self.workers if t.is_alive()]
            self.workers.append((group,worker));worker.start()

    def do_controller_start(self,body):
        # The web entry must start acquisition, not assume yesterday's ROS nodes survived reboot.
        sensor_start=subprocess.run(['systemctl','--user','start','pika-collection-sensors.service'],
                                    capture_output=True,text=True,timeout=20)
        if sensor_start.returncode:
            raise RuntimeError('Sense/Tracker 采集服务启动失败：'+sensor_start.stderr[-1200:])
        try:
            current=self.call('status',2)
        except (OSError,ValueError):current=None
        if current:
            if not current.get('execute'):raise ValueError('当前是只读预览，请先退出预览')
            if current.get('error'):
                raise RuntimeError('控制器仍处于故障状态，需停止旧控制器后重新启动：'+str(current['error']))
            self.log('控制服务已运行');return
        logfile=ROOT/'acceptance'/('web_controller_'+time.strftime('%Y%m%d_%H%M%S')+'.log')
        logfile.parent.mkdir(exist_ok=True)
        with logfile.open('w') as log:
            self.controller=subprocess.Popen(['bash',str(ROOT/'switch.sh'),'serve','--execute'],stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if self.controller.poll() is not None:raise RuntimeError(logfile.read_text()[-2200:])
            try:
                status=self.call('status',1)
                if status.get('error'):raise RuntimeError(status['error'])
                self.log('控制就绪，双臂保持当前位置；请选择左臂或右臂');return
            except OSError:time.sleep(.1)
        raise RuntimeError('启动超时，请查看 '+str(logfile))

    def control_command(self,command):
        status=self.call(command)
        with self.lock:self.control={'connected':True,**status}
        if status.get('error') and command not in ('pause','stop'):raise RuntimeError(status['error'])
    def do_left(self,body):self.control_command('left')
    def do_right(self,body):self.control_command('right')
    def do_pause(self,body):self.control_command('pause')
    def do_controller_stop(self,body):
        if self.collection_session or self.record:raise ValueError('请先退出采集会话')
        self.control_command('stop')
        if self.controller:self.controller.wait(timeout=12)

    def validate_config(self,body):
        task=body.get('task',self.config['task']);task_path(self.root,task)
        frames=body.get('max_frames',self.config['max_frames']);fps=body.get('fps',self.config['fps'])
        if type(frames) is not int or not 1<=frames<=1000000:raise ValueError('最大帧数须为 1–1000000 的整数')
        if type(fps) is not int or not 1<=fps<=30:raise ValueError('当前三相机采集帧率须为 1–30 Hz')
        prompt=body.get('prompt',self.config.get('prompt',''))
        if not isinstance(prompt,str) or len(prompt)>2000:raise ValueError('任务描述须少于 2000 字')
        return dict(task=task,prompt=prompt.strip(),max_frames=frames,fps=fps)

    def do_config_save(self,body):
        if self.collection_session or self.record:raise ValueError('请先退出采集会话再修改配置')
        config=self.validate_config(body)
        path=self.root/'.console_config.json';temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2));temporary.replace(path)
        with self.lock:self.config=config
        self.log('采集配置已保存')

    def acquire_record_lock(self):
        if self.record_lock:return
        lock=(ROOT/'.session.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:lock.close();raise ValueError('其他采集会话正在使用录制入口')
        self.record_lock=lock

    def do_session_start(self,body):
        if self.collection_session or self.record:raise ValueError('采集会话已启动')
        config=self.validate_config(body)
        status=self.call('status',2)
        if not status.get('execute') or status.get('error') or status.get('phase') not in ('active','paused'):
            raise ValueError('请先启动双臂控制并检查数据状态')
        self.acquire_record_lock()
        try:
            task=task_path(self.root,config['task']);task.mkdir(parents=True,exist_ok=True)
            profile=json.loads((ROOT/'profile_single_sense_dual.json').read_text());profile['hz']=config['fps']
            profile_path=task/'.capture_profile.json';profile_path.write_text(json.dumps(profile,indent=2))
            check=subprocess.run(['bash',str(ROOT/'run.sh'),'--profile',str(profile_path),'streams'],capture_output=True,text=True,timeout=15)
            if check.returncode:raise RuntimeError('采集数据未就绪：'+(check.stdout+check.stderr)[-1500:])
            with self.lock:self.collection_session={**config,'id':secrets.token_hex(8),'profile':str(profile_path)}
            self.log('交互采集会话已启动，等待 Enter 开始录制')
        except Exception:self.release_record_lock();raise

    def do_start(self,body):
        if self.record:raise ValueError('已有一条正在录制或等待处理')
        control=self.call('status',2)
        if not control.get('execute') or control.get('error') or control.get('phase') not in ('active','paused'):
            raise ValueError('控制服务未就绪；先启动控制并检查状态')
        if self.collection_session:body={**body,**self.collection_session}
        name=body.get('task','');task=task_path(self.root,name);prompt=body.get('prompt','').strip()
        if self.collection_session and not prompt:prompt=name
        if not prompt or len(prompt)>2000:raise ValueError('请填写任务提示词（不超过 2000 字）')
        self.acquire_record_lock()
        try:
            raw=task/'raw';raw.mkdir(parents=True,exist_ok=True);(task/'hdf5').mkdir(exist_ok=True)
            ident=secrets.token_hex(8);logfile=task/f'record_{ident}.log'
            rec={'id':ident,'task':name,'prompt':prompt,'phase':'starting','frames':0,'started_at':time.time(),
                 'before':set(raw.glob('episode_*')),'raw':raw,'episode':None,'logfile':logfile}
            profile=self.collection_session['profile'] if self.collection_session else str(ROOT/'profile_single_sense_dual.json')
            command=['bash',str(ROOT/'run.sh'),'--profile',profile,'record','--task',prompt,'--output',str(raw)]
            rec['auto_save']=bool(self.collection_session)
            if self.collection_session:command+=['--max-frames',str(body['max_frames'])]
            with logfile.open('x') as log:
                rec['process']=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            with self.lock:self.record=rec
            threading.Thread(target=self.monitor_record,args=(rec,),daemon=True).start()
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                if rec['phase']=='recording':self.log('开始录制：'+name);return
                if rec['process'].poll() is not None:raise RuntimeError(logfile.read_text()[-1500:])
                time.sleep(.1)
            raise RuntimeError('录制就绪超时；请删除本条或退出录制后检查日志')
        except Exception:
            if self.record is None and not self.collection_session:self.release_record_lock()
            raise

    def monitor_record(self,rec):
        while not self.closed.is_set():
            text=rec['logfile'].read_text(errors='replace')
            with self.lock:
                if self.record is not rec:return
                if 'Recording ' in text and rec['phase']=='starting':rec['phase']='recording'
                counts=re.findall(r'^(\d+) frames,',text,re.M)
                if counts:rec['frames']=int(counts[-1])
                paths=set(rec['raw'].glob('episode_*'))-rec['before']
                if len(paths)==1:rec['episode']=str(next(iter(paths)))
                code=rec['process'].poll()
                if code is not None:
                    if rec['phase'] in ('saving','discarding'):return
                    if code==0 and rec.get('auto_save'):
                        if not self.jobs.get('record',{}).get('running'):
                            try:self.submit('save',{'recording_id':rec['id']})
                            except ValueError:pass
                            else:self.log('达到最大帧数，正在自动保存');return
                    else:
                        rec['phase']='failed';rec['error']=text[-1600:];return
            self.closed.wait(.5)

    def release_record_lock(self):
        if self.record_lock:self.record_lock.close();self.record_lock=None

    def finish(self,save):
        rec=self.record
        if not rec:return
        with self.lock:rec['phase']='saving' if save else 'discarding'
        child=rec['process']
        if child.poll() is None:child.send_signal(signal.SIGINT)
        try:code=child.wait(timeout=180)
        except subprocess.TimeoutExpired:raise RuntimeError('录制仍在校验；原始数据已保留，请稍后重试')
        paths=set(rec['raw'].glob('episode_*'))-rec['before']
        if len(paths)!=1:
            with self.lock:self.record=None
            if not self.collection_session:self.release_record_lock()
            raise RuntimeError('没有生成唯一数据条目，请检查 '+str(rec['logfile']))
        episode=paths.pop();review={'accepted':False,'reviewed_at':time.time(),'reason':'discarded' if not save else 'validation_pending'}
        destination=None
        try:
            if save:
                if code:
                    quality_path=episode/'quality.json'
                    quality=json.loads(quality_path.read_text()) if quality_path.exists() else {}
                    detail='；'.join(quality.get('failure_reasons',[]))
                    if not detail:
                        manifest=json.loads((episode/'manifest.json').read_text())
                        detail=manifest.get('error','请查看该条 quality.json 和录制日志')
                    raise RuntimeError('录制质量检查失败：'+detail+'；保留原始数据：'+str(episode))
                from export_aloha import export
                hdf=rec['raw'].parent/'hdf5'
                indices=[int(p.stem.split('_')[-1]) for p in hdf.glob('episode_*.hdf5') if p.stem.split('_')[-1].isdigit()]
                destination=hdf/f'episode_{max(indices,default=-1)+1}.hdf5'
                export(episode,destination)
                review.update(accepted=True,reason='saved',hdf5=str(destination))
            (episode/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
            with self.lock:self.last_result={'saved':save,'episode':str(episode),'hdf5':str(destination) if destination else None}
            self.log(('保存完成：'+str(destination)) if save else ('本条未纳入数据集，原始文件保留：'+str(episode)))
        finally:
            if not (episode/'review.json').exists():(episode/'review.json').write_text(json.dumps(review))
            with self.lock:self.record=None
            if not self.collection_session:self.release_record_lock()

    def do_save(self,body):self.finish(True)
    def do_discard(self,body):self.finish(False)
    def do_exit(self,body):
        # Pause first; recording continues through the hold transition.
        if (ROOT/'.switch.sock').exists():self.control_command('pause')
        self.finish(False)
        with self.lock:self.collection_session=None
        self.release_record_lock()
        self.log('已退出录制并暂停跟随；已保存条目不受影响')

    def episodes(self,name):
        task=task_path(self.root,name);items=[]
        for p in sorted((task/'raw').glob('episode_*'),reverse=True):
            try:
                meta=json.loads((p/'manifest.json').read_text());review=json.loads((p/'review.json').read_text()) if (p/'review.json').exists() else {}
                quality=json.loads((p/'quality.json').read_text()) if (p/'quality.json').exists() else {}
                items.append({'id':p.name,'status':'saved' if review.get('accepted') else meta.get('status','unknown') if not review else 'discarded',
                    'frames':quality.get('frames'),'valid':quality.get('valid'),'duration_s':quality.get('duration_s'),
                    'hdf5':review.get('hdf5'),'task':meta.get('task'),'started_at':meta.get('started_at')})
            except (OSError,ValueError):continue
        return items

    def catalog(self):
        tasks=[];converted=[]
        for task in sorted(self.root.iterdir()):
            if not task.is_dir() or task.name.startswith('.'):continue
            try:task_path(self.root,task.name)
            except ValueError:continue
            rows=self.episodes(task.name);saved=[x for x in rows if x['status']=='saved']
            tasks.append({'name':task.name,'episodes':len(saved),'frames':sum(x['frames'] or 0 for x in saved),
                          'bytes':sum(p.stat().st_size for p in (task/'hdf5').glob('episode_*.hdf5'))})
            for output in sorted(task.glob('lerobot28_*')):
                if not output.is_dir():continue
                try:
                    info=json.loads((output/'meta/info.json').read_text())
                    converted.append({'task':task.name,'name':output.name,'frames':info['total_frames'],
                                      'episodes':info['total_episodes'],'fps':info['fps'],'action_dim':info['features']['action']['shape'][0],
                                      'path':str(output),'version':info.get('codebase_version')})
                except (OSError,ValueError,KeyError):continue
        disk=shutil.disk_usage(self.root)
        return {'tasks':tasks,'converted':converted,'disk':{'total':disk.total,'used':disk.used,'free':disk.free}}

    def hdf_path(self,task_name,episode_name):
        task=task_path(self.root,task_name)
        if not re.fullmatch(r'episode_\d{8}_\d{6}_\d+',episode_name):raise ValueError('条目无效')
        episode=(task/'raw'/episode_name).resolve()
        if episode.parent!=(task/'raw').resolve():raise ValueError('条目路径无效')
        review=json.loads((episode/'review.json').read_text())
        if not review.get('accepted') or not review.get('hdf5'):raise ValueError('此条尚未保存为 HDF5')
        path=Path(review['hdf5']).resolve()
        if path.parent!=(task/'hdf5').resolve() or not path.is_file():raise ValueError('HDF5 路径无效')
        return path

    def episode_detail(self,task_name,episode_name):
        import h5py
        path=self.hdf_path(task_name,episode_name)
        with h5py.File(path,'r') as f:
            count=len(f['action']);step=max(1,(count+399)//400)
            return {'frames':count,'fps':float(f.attrs['fps']),'qpos_shape':list(f['observations/qpos'].shape),
                    'action_shape':list(f['action'].shape),'cameras':list(f['observations/images']),
                    'qpos':f['observations/qpos'][::step].tolist(),'step':step,'path':str(path)}

    def episode_image(self,task_name,episode_name,camera,frame):
        import h5py,io
        from PIL import Image
        if camera not in ('cam_high','cam_left_wrist','cam_right_wrist'):raise ValueError('相机无效')
        with h5py.File(self.hdf_path(task_name,episode_name),'r') as f:
            data=f['observations/images/'+camera]
            if not 0<=frame<len(data):raise ValueError('帧号超出范围')
            image=data[frame]
        stream=io.BytesIO();Image.fromarray(image).save(stream,format='JPEG',quality=85)
        return stream.getvalue()

    def do_delete_last(self,body):
        saved=[row for row in self.episodes(body.get('task','')) if row['status']=='saved']
        if not saved:raise ValueError('没有已保存的轨迹可删除')
        latest=max(saved,key=lambda row:int(Path(row['hdf5']).stem.split('_')[-1]))
        if body.get('episode')!=latest['id']:raise ValueError('最近保存条目已变化，请刷新列表后重试')
        self.do_delete(body)

    def do_delete(self,body):
        if self.record:raise ValueError('请先结束当前录制')
        if self.collection_session and body.get('task')!=self.collection_session['task']:raise ValueError('采集会话期间只能操作当前任务')
        task=task_path(self.root,body.get('task',''));name=body.get('episode','')
        if not re.fullmatch(r'episode_\d{8}_\d{6}_\d+',name):raise ValueError('数据条目无效')
        episode=(task/'raw'/name).resolve()
        if episode.parent!=(task/'raw').resolve() or not episode.is_dir():raise ValueError('条目不存在')
        review=json.loads((episode/'review.json').read_text()) if (episode/'review.json').exists() else {}
        hdf=Path(review['hdf5']).resolve() if review.get('hdf5') else None
        if hdf and hdf.parent!=(task/'hdf5').resolve():raise ValueError('HDF5 来源无效')
        trash=task/'.trash'/(name+'_'+secrets.token_hex(4));trash.mkdir(parents=True)
        if hdf and hdf.exists():shutil.move(str(hdf),trash/hdf.name)
        shutil.move(str(episode),trash/name)
        self.log('已移入回收站：'+str(trash)+'；已有转换副本不会自动删除')

    def do_convert(self,body):
        task=task_path(self.root,body.get('task',''))
        if not list((task/'hdf5').glob('episode_*.hdf5')):raise ValueError('没有已保存的 HDF5 数据')
        prompt=body.get('prompt','').strip()
        if not prompt:raise ValueError('请填写转换使用的任务提示词')
        fps=body.get('fps',30)
        if type(fps) is not int or not 1<=fps<=30:raise ValueError('FPS 无效')
        output=task/('lerobot28_v21_'+time.strftime('%Y%m%d_%H%M%S'));logfile=task/(output.name+'.log')
        env=os.environ.copy();env['LEROBOT_PYTHON']=sys.executable
        with logfile.open('x') as log:
            subprocess.run(['bash',str(ROOT/'convert_lerobot.sh'),'--input_dir',str(task/'hdf5'),
                '--output_dir',str(output),'--prompt',prompt,'--fps',str(fps),'--action-layout','legacy28','--version','v2.1'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run([sys.executable,str(ROOT/'verify_lerobot.py'),str(task/'hdf5'),str(output)],stdout=log,stderr=subprocess.STDOUT,check=True)
        self.log('LeRobot v2.1 / 28 维转换与校验完成：'+str(output))

    def close(self):
        # Finish in-flight control/record operations before closing their resources.
        with self.lock:self.closing=True;workers=list(self.workers)
        for group,worker in workers:
            if group!='convert':worker.join(timeout=185)
        # Service stop also ends its own control session without disabling motors.
        try:
            if self.controller and self.controller.poll() is None:self.call('pause')
            if self.record:self.finish(False)
            if self.controller and self.controller.poll() is None:self.call('stop');self.controller.wait(timeout=15)
        finally:self.closed.set();self.release_record_lock()


def make_handler(console):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,code,body,mime='application/json; charset=utf-8'):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY');self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path=='/':return self.send(200,(ROOT/'web_console.html').read_text().replace('__CSRF_TOKEN__',token).encode(),'text/html; charset=utf-8')
                if path in ('/static/vue.global.prod.js','/static/tailwind.min.css'):
                    return self.send(200,(ROOT/path.lstrip('/')).read_bytes(),'text/javascript' if path.endswith('.js') else 'text/css')
                if path=='/api/status':return self.send(200,console.snapshot())
                if path=='/api/catalog':return self.send(200,console.catalog())
                if path in ('/api/episode/detail','/api/episode/image'):
                    query=parse_qs(urlsplit(self.path).query)
                    task=query.get('task',[''])[0];episode=query.get('episode',[''])[0]
                    if path.endswith('detail'):return self.send(200,console.episode_detail(task,episode))
                    return self.send(200,console.episode_image(task,episode,query.get('camera',[''])[0],int(query.get('frame',['0'])[0])),'image/jpeg')
                if path=='/api/tasks':return self.send(200,[p.name for p in console.root.iterdir() if p.is_dir() and not p.name.startswith('.')])
                if path=='/api/episodes':return self.send(200,console.episodes(parse_qs(urlsplit(self.path).query).get('task',[''])[0]))
                if path.startswith('/camera/') and path.split('/')[-1] in CAMERAS:
                    serial=CAMERAS[path.split('/')[-1]]
                    with urlopen('http://127.0.0.1:8080/frame/'+serial+'.jpg',timeout=2) as response:
                        return self.send(200,response.read(),'image/jpeg')
                return self.send(404,{'error':'Not found'})
            except Exception as exc:return self.send(503,{'error':str(exc)})
        def do_POST(self):
            origin=self.headers.get('Origin')
            if self.headers.get('X-Console-Token')!=token or (origin and urlsplit(origin).netloc!=self.headers.get('Host')):
                return self.send(403,{'error':'请从本控制台页面操作'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8192:raise ValueError('请求大小无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise ValueError('参数无效')
                if self.path!='/api/action':return self.send(404,{'error':'Not found'})
                console.submit(body.get('action'),body)
                return self.send(202,{'ok':True})
            except Exception as exc:return self.send(409,{'error':str(exc)})
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=8081)
    parser.add_argument('--data-root',default=str(Path.home()/'datasets/pika_piper'));args=parser.parse_args()
    lock=(ROOT/'.web_console.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    console=Console(args.data_root);server=ThreadingHTTPServer((args.host,args.port),make_handler(console))
    def stop(*_):threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    print(f'Piper collection console: http://{args.host}:{args.port}',flush=True)
    try:server.serve_forever()
    finally:server.server_close();console.close()


if __name__=='__main__':main()
