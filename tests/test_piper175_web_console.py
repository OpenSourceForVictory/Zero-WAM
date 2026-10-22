"""Control-console state and data ownership checks; never opens CAN or ROS."""
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import urllib.error
import pytest

ROOT=Path(__file__).resolve().parents[1]/'evaluation/piper175/collection'
sys.path.insert(0,str(ROOT))
import web_console as web


@pytest.fixture
def console(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda self:None)
    c=web.Console(tmp_path/'data')
    yield c
    c.closed.set()


def test_task_paths_reject_escape(tmp_path):
    for value in ['../outside','a/b','', '.', '/tmp','a; echo oops']:
        with pytest.raises(ValueError):web.task_path(tmp_path,value)
    assert web.task_path(tmp_path,'双臂_测试-1').parent==tmp_path
    (tmp_path/'link').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(ValueError):web.task_path(tmp_path,'link')


def test_inference_preferences_persist_without_safety_confirmation(console,monkeypatch):
    monkeypatch.setattr(console,'inference_catalog',lambda:[{'video_id':'V01'}])
    first={'video_id':'V01','profile':'r8000','mode':'execute','max_chunks':32,
           'tab':'inference','updated_at':100,'ready':True,'prompt':'unchecked'}
    saved=console.save_inference_preferences(first)
    assert console.inference_preferences()==saved
    assert 'ready' not in saved and 'prompt' not in saved
    assert console.save_inference_preferences({**first,'profile':'29','updated_at':99})['profile']=='29'
    with pytest.raises(ValueError,match='示教'):
        console.save_inference_preferences({**first,'video_id':'missing','updated_at':101})


def test_stale_recording_id_cannot_discard_new_take(console):
    console.record={'id':'new-take'}
    with pytest.raises(ValueError,match='状态已变化'):
        console.submit('discard',{'recording_id':'old-take'})


def test_control_start_requires_explicit_readiness(console):
    with pytest.raises(ValueError,match='现场'):
        console.submit('controller_start',{})


def test_busy_record_rejects_conversion_but_allows_pause(console,monkeypatch):
    console.jobs['record']={'running':True}
    with pytest.raises(ValueError):console.submit('convert',{})
    called=threading.Event()
    monkeypatch.setattr(console,'do_pause',lambda body:called.set())
    console.submit('pause',{})
    assert called.wait(1)


def test_saved_episode_deletion_preserves_raw_in_trash(console):
    task=web.task_path(console.root,'test');raw=task/'raw'/'episode_20261015_120000_1';raw.mkdir(parents=True)
    hdf=task/'hdf5'/'episode_0.hdf5';hdf.parent.mkdir();hdf.write_bytes(b'preserved')
    (raw/'review.json').write_text(json.dumps({'accepted':True,'hdf5':str(hdf)}))
    console.do_delete({'task':'test','episode':raw.name})
    assert not raw.exists() and not hdf.exists()
    moved=list((task/'.trash').iterdir());assert len(moved)==1
    assert (moved[0]/hdf.name).read_bytes()==b'preserved'
    assert (moved[0]/raw.name/'review.json').exists()


def test_deletion_rejects_hdf_outside_task(console,tmp_path):
    raw=console.root/'test'/'raw'/'episode_20261015_120000_1';raw.mkdir(parents=True)
    outside=tmp_path/'other.hdf5';outside.write_bytes(b'untouched')
    (raw/'review.json').write_text(json.dumps({'hdf5':str(outside)}))
    with pytest.raises(ValueError):console.do_delete({'task':'test','episode':raw.name})
    assert outside.read_bytes()==b'untouched' and raw.exists()


def test_exit_pauses_before_discard(console,monkeypatch,tmp_path):
    monkeypatch.setattr(web,'ROOT',tmp_path);(tmp_path/'.switch.sock').touch();events=[]
    monkeypatch.setattr(console,'control_command',lambda cmd:events.append(cmd))
    monkeypatch.setattr(console,'finish',lambda save:events.append(save))
    console.do_exit({});assert events==['pause',False]


def test_finish_discard_retains_raw_and_marks_unaccepted(console):
    class Process:
        def poll(self):return 0
        def wait(self,timeout):return 0
    raw=console.root/'test'/'raw';ep=raw/'episode_20261015_120000_1';ep.mkdir(parents=True)
    (ep/'messages.jsonl').write_text('keep me')
    console.record={'raw':raw,'before':set(),'process':Process(),'phase':'recording'}
    console.finish(False)
    assert console.record is None
    assert json.loads((ep/'review.json').read_text())['accepted'] is False
    assert (ep/'messages.jsonl').read_text()=='keep me'


def test_http_get_does_not_control_and_post_requires_token(console,monkeypatch):
    called=[];monkeypatch.setattr(console,'submit',lambda a,b:called.append(a))
    server=web.ThreadingHTTPServer(('127.0.0.1',0),web.make_handler(console))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        page=urllib.request.urlopen(base).read().decode()
        assert '启动交互采集' in page and not called
        request=urllib.request.Request(base+'/api/action',data=b'{"action":"left"}',headers={'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        assert error.value.code==403 and not called
        import re
        token=re.search("const token='([^']+)'",page)[1]
        request.add_header('X-Console-Token',token)
        request.add_header('Origin','http://unrelated.invalid')
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(request)
        request.remove_header('Origin')
        assert urllib.request.urlopen(request).status==202 and called==['left']
    finally:server.shutdown();server.server_close();thread.join()

def test_start_save_workflow_keeps_episode_identity_and_exports(console,monkeypatch,tmp_path):
    import export_aloha
    monkeypatch.setattr(web,'ROOT',tmp_path)
    monkeypatch.setattr(web,'rpc',lambda *a,**k:{'execute':True,'phase':'paused','error':None})
    class Process:
        def __init__(self,args,stdout,stderr):
            raw=Path(args[args.index('--output')+1]);ep=raw/'episode_20261015_120000_42';ep.mkdir()
            stdout.write('Recording '+str(ep)+'\n');stdout.flush();self.code=None
        def poll(self):return self.code
        def send_signal(self,sig):self.code=0
        def wait(self,timeout):return self.code
    monkeypatch.setattr(web.subprocess,'Popen',Process)
    monkeypatch.setattr(export_aloha,'export',lambda episode,dest:dest.write_bytes(b'exported'))
    console.do_start({'task':'test','prompt':'transfer object'})
    assert console.record['phase']=='recording'
    console.do_save({})
    assert console.record is None and console.record_lock is None
    task=console.root/'test'
    assert (task/'hdf5/episode_0.hdf5').read_bytes()==b'exported'
    review=json.loads((task/'raw/episode_20261015_120000_42/review.json').read_text())
    assert review['accepted'] and review['hdf5'].endswith('episode_0.hdf5')

def test_status_query_waits_for_switch_rpc(console,monkeypatch):
    entered=threading.Event();release=threading.Event();queried=threading.Event()
    def fake_rpc(command,timeout=45):
        if command=='left':entered.set();assert release.wait(2)
        else:queried.set()
        return {'phase':'active','error':None}
    monkeypatch.setattr(web,'rpc',fake_rpc)
    control=threading.Thread(target=console.control_command,args=('left',));control.start()
    assert entered.wait(1)
    query=threading.Thread(target=console.call,args=('status',));query.start()
    assert not queried.wait(.1)
    release.set();control.join(2);query.join(2)
    assert queried.is_set()

def test_delete_last_rejects_stale_list(console,monkeypatch):
    rows=[{'id':'old','status':'saved','hdf5':'/test/episode_0.hdf5'},
          {'id':'new','status':'saved','hdf5':'/test/episode_1.hdf5'}]
    monkeypatch.setattr(console,'episodes',lambda name:rows)
    calls=[];monkeypatch.setattr(console,'do_delete',lambda body:calls.append(body['episode']))
    with pytest.raises(ValueError,match='已变化'):
        console.do_delete_last({'task':'test','episode':'old'})
    assert not calls
    console.do_delete_last({'task':'test','episode':'new'})
    assert calls==['new']


def test_session_configuration_and_failed_preflight_release_lock(console,monkeypatch,tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(web,'ROOT',tmp_path)
    (tmp_path/'profile_single_sense_dual.json').write_text('{"hz":10}')
    monkeypatch.setattr(console,'call',lambda *a:{'execute':True,'phase':'paused'})
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='missing camera',stderr=''))
    with pytest.raises(RuntimeError,match='missing camera'):
        console.do_session_start({'task':'test','fps':20,'max_frames':50})
    assert console.record_lock is None and console.collection_session is None
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    console.do_session_start({'task':'test','fps':20,'max_frames':50})
    assert console.collection_session['max_frames']==50
    assert json.loads(Path(console.collection_session['profile']).read_text())['hz']==20
    with pytest.raises(ValueError):console.do_config_save({'task':'other'})
    with pytest.raises(ValueError):console.submit('convert',{})
    with pytest.raises(ValueError):console.do_controller_stop({})
    console.do_exit({})
    assert console.record_lock is None and console.collection_session is None


def test_auto_save_keeps_session_for_next_take(console,monkeypatch,tmp_path):
    import export_aloha
    monkeypatch.setattr(web,'ROOT',tmp_path)
    console.collection_session={'id':'session','task':'test','prompt':'','profile':'test.json','fps':10,'max_frames':5}
    monkeypatch.setattr(console,'call',lambda *a:{'execute':True,'phase':'paused'})
    monkeypatch.setattr(export_aloha,'export',lambda episode,dest:dest.write_bytes(b'exported'))
    commands=[]
    class Process:
        def __init__(self,args,stdout,stderr):
            commands.append(args);raw=Path(args[args.index('--output')+1])
            (raw/f'episode_20261016_120000_{len(commands)}').mkdir()
            stdout.write('Recording test\n5 frames, elapsed 0.5s\n');stdout.flush()
        def poll(self):return 0
        def wait(self,timeout):return 0
    monkeypatch.setattr(web.subprocess,'Popen',Process)
    for i in range(2):
        console.submit('start',{})
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            if console.last_result and console.last_result['hdf5'].endswith(f'episode_{i}.hdf5') and not console.jobs['record']['running']:break
            time.sleep(.05)
        else:pytest.fail(str(console.snapshot()))
        assert console.record is None and console.record_lock is not None
    assert all(c[c.index('--max-frames')+1]=='5' for c in commands)
    console.do_exit({});assert console.record_lock is None


def test_detail_and_image_enforce_dataset_boundaries(console,tmp_path):
    import numpy as np
    import h5py
    task=console.root/'test';raw=task/'raw/episode_20261016_120000_1';raw.mkdir(parents=True)
    hdf=task/'hdf5/episode_0.hdf5';hdf.parent.mkdir()
    with h5py.File(hdf,'w') as f:
        f.attrs['fps']=10;f['action']=np.zeros((3,14));f['observations/qpos']=np.zeros((3,14))
        f['observations/images/cam_high']=np.zeros((3,4,4,3),dtype=np.uint8)
    (raw/'review.json').write_text(json.dumps({'accepted':True,'hdf5':str(hdf)}))
    assert console.episode_detail('test',raw.name)['frames']==3
    assert console.episode_image('test',raw.name,'cam_high',0).startswith(b'\xff\xd8')
    with pytest.raises(ValueError):console.episode_image('test',raw.name,'cam_high',3)
    with pytest.raises(ValueError):console.hdf_path('test','../outside')
    (raw/'review.json').write_text(json.dumps({'accepted':True,'hdf5':str(tmp_path/'outside.hdf5')}))
    with pytest.raises(ValueError):console.hdf_path('test',raw.name)


def test_latched_fault_does_not_block_successful_stop(console,monkeypatch):
    monkeypatch.setattr(console,'call',lambda command:{'phase':'paused','error':'previous stale CAN'})
    console.control_command('pause')
    console.control_command('stop')
    with pytest.raises(RuntimeError,match='previous stale CAN'):console.control_command('left')
    def failed(command):raise RuntimeError('fresh command failure')
    monkeypatch.setattr(console,'call',failed)
    with pytest.raises(RuntimeError,match='fresh command failure'):console.control_command('pause')


def test_start_reports_existing_controller_fault(console,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(web.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    monkeypatch.setattr(console,'call',lambda *a:{'execute':True,'error':'stale CAN'})
    with pytest.raises(RuntimeError,match='stale CAN'):
        console.do_controller_start({'ready':True})


def test_relocalize_requires_stationary_and_no_recording(console):
    with pytest.raises(ValueError,match='放稳'):console.submit('relocalize',{})
    console.record={'id':'take'}
    with pytest.raises(ValueError,match='结束当前录制'):console.submit('relocalize',{'stationary':True})
    console.record=None;console.jobs['record']={'running':True}
    with pytest.raises(ValueError,match='结束当前录制'):console.submit('relocalize',{'stationary':True})


def test_relocalize_blocks_recording_and_arm_switch(console):
    console.jobs['control']={'running':True,'action':'relocalize'}
    for action in ('start','session_start'):
        with pytest.raises(ValueError,match='重新定位'):console.submit(action,{})
    with pytest.raises(ValueError,match='正在处理'):console.submit('left',{})


def test_relocalize_runs_worker(console,monkeypatch):
    called=threading.Event()
    monkeypatch.setattr(console,'do_relocalize',lambda body:called.set())
    console.submit('relocalize',{'stationary':True})
    assert called.wait(1)


def test_zero_requires_valid_selection_readiness_and_idle_record(console):
    with pytest.raises(ValueError,match='选择'):
        console.submit('enable_zero',{'arm':'invalid','ready':True})
    with pytest.raises(ValueError,match='清空'):
        console.submit('enable_zero',{'arm':'both'})
    for invalid in (0,7,5.0,True):
        with pytest.raises(ValueError,match='速度'):
            console.submit('enable_zero',{'arm':'both','ready':True,'speed_percent':invalid})
    console.record={'id':'active'}
    with pytest.raises(ValueError,match='录制'):
        console.submit('enable_zero',{'arm':'both','ready':True})


def test_zero_blocks_recording_and_other_control(console):
    console.jobs['control']={'running':True,'action':'enable_zero'}
    with pytest.raises(ValueError,match='归零'):
        console.submit('start',{})
    with pytest.raises(ValueError,match='处理'):
        console.submit('left',{})


def test_zero_driver_fault_screen():
    import enable_zero
    enable_zero.check_flags('driver_overcurrent: False\nstall_status: False')
    for flag in ['driver_overcurrent','motor_overheating','sensor_status','stall_status']:
        with pytest.raises(RuntimeError,match=flag):
            enable_zero.check_flags(flag+': True')


def test_zero_failure_stops_before_second_arm(tmp_path,monkeypatch):
    import enable_zero as zero
    import subprocess
    root=tmp_path/'collection';root.mkdir()
    monkeypatch.setattr(zero,'ROOT',root)
    monkeypatch.setattr(zero,'RUNTIME',tmp_path)
    monkeypatch.setattr(zero,'controllers',lambda:[])
    monkeypatch.setattr(zero,'check_arm',lambda side:None)
    monkeypatch.setattr(sys,'argv',['enable_zero.py','--arm','both','--speed-percent','5'])
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        if '--execute' in cmd:raise subprocess.CalledProcessError(1,cmd)
    monkeypatch.setattr(zero.subprocess,'run',run)
    with pytest.raises(subprocess.CalledProcessError):zero.main()
    moves=[cmd for cmd in calls if '--execute' in cmd]
    assert len(moves)==1 and moves[0][moves[0].index('--arm')+1]=='left'
    assert moves[0][moves[0].index('--speed-percent')+1]=='5'
    assert calls[-1][-1]=='stop'


def test_inference_requires_readiness_and_known_demo(console,monkeypatch):
    monkeypatch.setattr(console,'inference_catalog',lambda:[{'video_id':'V01','latent_path':'/demo.pth'}])
    body={'mode':'execute','ready':False,'max_chunks':1,'port':39057,'prompt':'task','video_id':'V01'}
    with pytest.raises(ValueError,match='确认'):console.submit('inference_start',body)
    body.update(ready=True,video_id='unknown')
    with pytest.raises(ValueError,match='示教'):console.submit('inference_start',body)


def test_inference_blocks_other_robot_operations_but_stop_works(console,monkeypatch):
    console.jobs['inference']={'running':True}
    for action in ('controller_start','enable_zero','session_start','start','relocalize'):
        with pytest.raises(ValueError,match='推理正在运行'):console.submit(action,{})
    called=threading.Event()
    monkeypatch.setattr(console,'do_inference_stop',lambda body:called.set())
    console.submit('inference_stop',{})
    assert called.wait(1)


def test_inference_rejects_active_collection(console,monkeypatch):
    monkeypatch.setattr(console,'validate_inference',lambda body:{})
    for attr,value in [('collection_session',{'task':'test'}),('control',{'connected':True}),('record',{'id':'test'})]:
        old=getattr(console,attr);setattr(console,attr,value)
        with pytest.raises(ValueError,match='退出采集'):console.submit('inference_start',{})
        setattr(console,attr,old)
