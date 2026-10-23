"""Archive isolation, retry and graceful stop tests; never opens CAN."""
import json
from pathlib import Path
import sys
import threading
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'evaluation/piper175/collection'))
from inference_broker import Broker, safe_run, safe_target, handler
from inference_archive_remote import decoder_command
import web_console as web


def test_archive_paths():
    for path in ('../x','/tmp/run','a/../../b','a;touch x'):
        with pytest.raises(ValueError):safe_run(path)
    assert safe_run('web/20261022/run_execute')=='web/20261022/run_execute'
    for path in ('/data/../etc','/data-other/x','relative'):
        with pytest.raises(ValueError):safe_target(path,'/data')
    assert safe_target('/data/custom test','/data')=='/data/custom test'


def test_r8000_archive_uses_cpu_decoder_script(tmp_path):
    script=tmp_path/'decode_predictions.py';script.write_text('')
    queue=tmp_path/'decode_work'/'piperpika_run'
    command=decoder_command({'decoder_script':str(script),'device':'cpu'},queue,'/model')
    assert command[2]==str(script)
    assert command[-2:]==['cpu','--once']
    assert '--root' in command and command[command.index('--root')+1]==str(queue.parent)
    with pytest.raises(FileNotFoundError):
        decoder_command({'decoder_script':str(tmp_path/'missing.py'),'device':'cpu'},queue,'/model')


def test_local_gpu_archive_uses_local_weights_and_preserves_source_checkpoint(tmp_path):
    script=tmp_path/'decode_predictions.py';script.write_text('')
    queue=tmp_path/'decode_work'/'piperpika_run'
    command=decoder_command({'decoder_script':str(script),'device':'cuda:0',
                             'source_checkpoint':'/r8000/model'},queue,'/local/model')
    assert command[command.index('--checkpoint')+1]=='/local/model'
    assert command[command.index('--source-checkpoint')+1]=='/r8000/model'
    assert command[command.index('--device')+1]=='cuda:0'


def test_corrected_demo_uses_versioned_latent_name(tmp_path,monkeypatch):
    source='/server/aligned_prompt/V05_aligned.pth'
    b=Broker({'state_dir':str(tmp_path),'demos':[{'video_id':'V05','latent_path':source}],
              'profiles':{'r8000':{'id':'r8000','server_port':29056,
                                   'demo_root':'/r8000/demos','client_host':'r8000','client_port':29056}}})
    transfers=[]
    monkeypatch.setattr(b,'ssh',lambda *a,**k:b'')
    def copy(host,src,dst,upload=False):
        transfers.append((host,src,dst,upload))
        if not upload:Path(dst).write_bytes(b'latent')
    monkeypatch.setattr(b,'copy',copy)
    b.config['demos'][0]['prompt']='aligned task'
    with pytest.raises(ValueError,match='配对已更新'):
        b.prepare({'profile':'r8000','video_id':'V05','prompt':'old task'})
    result=b.prepare({'profile':'r8000','video_id':'V05','prompt':'aligned task'})
    assert result['latent_path']=='/r8000/demos/V05_aligned.pth'
    assert transfers[0][1]==source
    assert transfers[1][2]==result['latent_path']


def test_no_prompt_uses_text_free_demo_on_both_servers(tmp_path,monkeypatch):
    source='/server/aligned_prompt/V05_aligned.pth'
    b=Broker({'state_dir':str(tmp_path),'demos':[{'video_id':'V05','latent_path':source,'prompt':'aligned task'}],
              'profiles':{'29':{'id':'29','server_port':29056,'client_host':'29','client_port':29056},
                          'r8000':{'id':'r8000','server_port':29056,'demo_root':'/r8000/demos',
                                   'client_host':'r8000','client_port':29056}}})
    calls=[];transfers=[]
    monkeypatch.setattr(b,'ssh',lambda host,command,**kw:calls.append((host,command)))
    def copy(host,src,dst,upload=False):
        transfers.append((host,src,dst,upload))
        if not upload:Path(dst).write_bytes(b'latent')
    monkeypatch.setattr(b,'copy',copy)
    body={'profile':'29','video_id':'V05','prompt':'aligned task','use_prompt':False}
    result=b.prepare(body)
    assert result['latent_path']=='/server/aligned_prompt/no_prompt/V05_aligned_noprompt.pth'
    assert result['use_prompt'] is False
    assert any(host=='29' and 'text_emb' in command[2] and 'pop' in command[2] for host,command in calls)
    result=b.prepare({**body,'profile':'r8000'})
    assert result['latent_path']=='/r8000/demos/V05_aligned_noprompt.pth'
    assert transfers[0][1]=='/server/aligned_prompt/no_prompt/V05_aligned_noprompt.pth'
    with pytest.raises(ValueError,match='模式无效'):
        b.prepare({**body,'use_prompt':'false'})


def test_restart_marks_unfinished_jobs_retryable(tmp_path):
    (tmp_path/'job_1.json').write_text(json.dumps({'status':'running'}))
    b=Broker({'state_dir':str(tmp_path)})
    assert b.jobs()[0]['status']=='failed'


def test_duplicate_complete_archive_is_idempotent(tmp_path):
    job=dict(id='1',status='complete',run='web/run',profile='29',target='/data/test')
    (tmp_path/'job_1.json').write_text(json.dumps(job))
    b=Broker({'state_dir':str(tmp_path),'profiles':{'29':{}},'archive_root':'/data'})
    assert b.enqueue(job)==job


def test_missing_saved_video_never_completes(tmp_path,monkeypatch):
    b=Broker({'state_dir':str(tmp_path),'ipc_log_root':'/robot/logs','demos':[],'hosts':{'ipc':{'ssh':['ssh','robot'],'address':'robot'}}})
    job=dict(id='x',run='web/run',profile='29',target='/data')
    incoming=tmp_path/'x/incoming';incoming.mkdir(parents=True)
    (incoming/'manifest.json').write_text('{}')
    (incoming/'result.json').write_text('{}')
    monkeypatch.setattr(b,'call',lambda *a,**k:b'')
    b.archive(job)
    assert job['status']=='failed' and 'combined.mp4' in job['error']


def test_incomplete_camera_recording_never_completes(tmp_path,monkeypatch):
    b=Broker({'state_dir':str(tmp_path),'ipc_log_root':'/robot/logs','demos':[],
              'hosts':{'ipc':{'ssh':['ssh','robot'],'address':'robot'}}})
    job=dict(id='x',run='web/run',profile='29',target='/data')
    incoming=tmp_path/'x/incoming';incoming.mkdir(parents=True)
    for name in ('manifest.json','result.json'):(incoming/name).write_text('{}')
    (incoming/'combined.mp4').write_bytes(b'video')
    monkeypatch.setattr(b,'call',lambda *a,**k:b'')
    b.archive(job)
    assert job['status']=='failed' and 'cam_high.mp4' in job['error']


def test_history_includes_unarchived_saved_runs(tmp_path,monkeypatch):
    runtime=tmp_path/'runtime';root=runtime/'collection';root.mkdir(parents=True)
    parent=runtime/'inference_client/inference_logs/web/20261022_191614'
    run=parent/'20261022_191614_509092_execute';run.mkdir(parents=True)
    (parent/'request.json').write_text(json.dumps({'request':{'video_id':'V02','profile':'r8000','mode':'execute','prompt':'pick object'}}))
    (run/'manifest.json').write_text('{}')
    (run/'result.json').write_text(json.dumps({'completed':False,'stopped_by_user':True,'sent_steps':439,'error':None}))
    monkeypatch.setattr(web,'ROOT',root)
    rows=web.Console.inference_history(None)
    assert len(rows)==1
    assert rows[0]['run']=='web/20261022_191614/20261022_191614_509092_execute'
    assert rows[0]['result']['sent_steps']==439
    (run/'result.json').unlink()
    assert web.Console.inference_history(None)[0]['finished'] is False


def test_manual_outcome_updates_and_success_rate(tmp_path,monkeypatch):
    root=tmp_path/'runtime/collection';root.mkdir(parents=True)
    run=root.parent/'inference_client/inference_logs/web/20261022_191614/20261022_191614_509092_execute'
    run.mkdir(parents=True)
    (run.parent/'request.json').write_text(json.dumps({'request':{'mode':'execute','video_id':'V02'}}))
    (run/'manifest.json').write_text(json.dumps({'mode':'execute'}))
    (run/'result.json').write_text('{}')
    monkeypatch.setattr(web,'ROOT',root)
    console=web.Console.__new__(web.Console)
    name='web/20261022_191614/20261022_191614_509092_execute'
    assert console.inference_review_stats()['with_prompt']['success_rate'] is None
    console.mark_inference_review({'run':name,'outcome':'success','note':'抓到物体，但未放进容器'})
    assert console.inference_history()[0]['review']['outcome']=='success'
    assert console.inference_history()[0]['review']['note']=='抓到物体，但未放进容器'
    assert console.inference_review_stats()['with_prompt']=={'success':1,'failure':0,'reviewed':1,'success_rate':100.0}
    console.mark_inference_review({'run':name,'outcome':'failure'})
    assert console.inference_history()[0]['review']['note']=='抓到物体，但未放进容器'
    assert console.inference_review_stats()['with_prompt']['success_rate']==0.0
    with pytest.raises(ValueError,match='备注'):
        console.mark_inference_review({'run':name,'outcome':'failure','note':'x'*1001})
    console.mark_inference_review({'run':name,'outcome':None})
    assert console.inference_review_stats()['with_prompt']['reviewed']==0
    assert console.inference_history()[0]['review']['note']=='抓到物体，但未放进容器'
    console.mark_inference_review({'run':name,'outcome':None,'note':''})
    assert console.inference_history()[0]['review'] is None
    console.mark_inference_review({'run':name,'outcome':None,'note':'场景光照较暗'})
    assert console.inference_review_stats()['with_prompt']['reviewed']==0
    assert console.inference_history()[0]['review']['note']=='场景光照较暗'
    (run/'result.json').unlink()
    with pytest.raises(ValueError,match='尚未结束'):
        console.mark_inference_review({'run':name,'outcome':'success'})


def test_history_and_success_rate_split_by_prompt_mode(tmp_path,monkeypatch):
    root=tmp_path/'runtime/collection';root.mkdir(parents=True)
    base=root.parent/'inference_client/inference_logs/web'
    for mode,outcome in [('with_prompt','success'),('no_prompt','failure')]:
        parent=base/('20261023_120000_'+mode)
        run=parent/('20261023_120001_'+mode+'_execute')
        run.mkdir(parents=True)
        request={'mode':'execute','video_id':'V05','prompt':'aligned task','use_prompt':mode=='with_prompt'}
        (parent/'request.json').write_text(json.dumps({'request':request}))
        (run/'manifest.json').write_text(json.dumps({'mode':'execute','prompt':request['prompt'] if request['use_prompt'] else ''}))
        (run/'result.json').write_text('{}')
        (run/'manual_evaluation.json').write_text(json.dumps({'outcome':outcome}))
    monkeypatch.setattr(web,'ROOT',root)
    console=web.Console.__new__(web.Console)
    history=console.inference_history()
    assert {(r['use_prompt'],r['prompt']) for r in history}=={(True,'aligned task'),(False,'')}
    stats=console.inference_review_stats()
    assert stats['with_prompt']['success_rate']==100.0
    assert stats['no_prompt']['success_rate']==0.0


def test_demo_preview_cached_once_and_range_is_bounded(tmp_path,monkeypatch):
    root=tmp_path/'runtime/collection';root.mkdir(parents=True)
    monkeypatch.setattr(web,'ROOT',root)
    console=web.Console.__new__(web.Console)
    console.demo_cache_lock=threading.Lock()
    monkeypatch.setattr(console,'inference_catalog',lambda:[{'video_id':'V02'}])
    requests=[]
    monkeypatch.setattr(console,'inference_bridge',lambda endpoint,binary=False:requests.append(endpoint) or b'video data')
    path=console.inference_demo_preview('V02')
    assert path.read_bytes()==b'video data'
    assert console.inference_demo_preview('V02')==path
    assert requests==['/demo-video?id=V02']
    with pytest.raises(ValueError):console.inference_demo_preview('../V02')
    assert web.video_byte_range('bytes=0-3',10)==(0,3)
    assert web.video_byte_range('bytes=-3',10)==(7,9)
    assert web.video_byte_range('bytes=8-',10)==(8,9)
    with pytest.raises(ValueError):web.video_byte_range('bytes=10-',10)


def test_completed_history_can_archive_while_inference_runs(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda self:None)
    console=web.Console(tmp_path/'data')
    console.jobs['inference']={'running':True,'action':'inference_start','error':None}
    calls=[]
    monkeypatch.setattr(console,'do_inference_archive',lambda body:calls.append(body['run']))
    console.submit('inference_archive',{'run':'web/older/run','target':'/data/archive'})
    console.workers[-1][1].join(timeout=2)
    assert calls==['web/older/run']
    console.jobs['inference']['running']=False
    console.closed.set()


def test_stop_does_not_interrupt_recording_finalization(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda self:None)
    c=web.Console(tmp_path/'data');p=Mock();p.poll.return_value=None;c.inference_process=p
    import inference_console
    calls=[];monkeypatch.setattr(inference_console.subprocess,'run',lambda *a,**k:calls.append(a))
    c.do_inference_stop({})
    assert c.inference_cancelled and calls
    p.send_signal.assert_not_called()
    c.closed.set()


def test_profile_prompt_must_match_encoded_demo(tmp_path,monkeypatch):
    monkeypatch.setattr(web.Console,'poll',lambda self:None)
    c=web.Console(tmp_path/'data');monkeypatch.setattr(c,'inference_catalog',lambda:[dict(video_id='V01',prompt='task',latent_path='/demo')])
    body=dict(profile='29',video_id='V01',prompt='other',ready=True,mode='execute',max_chunks=0)
    with pytest.raises(ValueError,match='prompt'):c.validate_inference(body)
    body['prompt']='task';assert c.validate_inference(body)['video_id']=='V01'
    body['profile']='unknown'
    with pytest.raises(ValueError):c.validate_inference(body)
    c.closed.set()


@pytest.mark.parametrize('use_prompt',[True,False])
def test_start_uses_selected_host_but_never_auto_uploads(tmp_path,monkeypatch,use_prompt):
    import inference_console
    import enable_zero
    runtime=tmp_path/'runtime';root=runtime/'collection';root.mkdir(parents=True)
    client=runtime/'inference_client';client.mkdir()
    monkeypatch.setattr(web,'ROOT',root);monkeypatch.setattr(web.Console,'poll',lambda self:None)
    c=web.Console(tmp_path/'data');monkeypatch.setattr(c,'validate_inference',lambda b:{'video_id':'V01','latent_path':'unused'})
    monkeypatch.setattr(c,'inference_bridge',lambda *a,**k:dict(host='192.0.2.1',port=29056,latent_path='/r8000/V01.pth'))
    monkeypatch.setattr(enable_zero,'controllers',lambda:[])
    monkeypatch.setattr(inference_console.subprocess,'run',lambda *a,**k:None)
    captured={};events=[]
    def popen(command,**kwargs):
        captured.update(command=command,env=kwargs['env'])
        output=Path(command[command.index('--save_dir')+1]);run=output/'test_execute';run.mkdir();(run/'manifest.json').write_text('{}')
        proc=Mock();proc.stdout=[];proc.wait.side_effect=lambda:events.append('exited') or 0;proc.poll.return_value=0
        return proc
    monkeypatch.setattr(inference_console.subprocess,'Popen',popen)
    monkeypatch.setattr(c,'do_inference_archive',lambda b:events.append('archive'))
    c.do_inference_start(dict(profile='r8000',prompt='task',use_prompt=use_prompt,mode='execute',max_chunks=0,auto_upload=True,upload_target='/results'))
    assert captured['command'][captured['command'].index('--prompt')+1]==('task' if use_prompt else '')
    assert Path(c.inference_result['output']).name.endswith('_with_prompt' if use_prompt else '_no_prompt')
    assert c.inference_result['use_prompt'] is use_prompt
    assert captured['command'][captured['command'].index('--server_host')+1]=='192.0.2.1'
    assert captured['env']['ZERO_WAM_ICL_LATENT_PATH']=='/r8000/V01.pth'
    assert captured['env']['ZERO_WAM_VIDEO_GUIDANCE_SCALE']==('1' if use_prompt else '-1')
    assert events==['exited']
    assert c.inference_result['run'].endswith('/test_execute')
    c.closed.set()


def test_progress_parser():
    from archive_progress import progress_fields
    assert progress_fields('    1,234  50%  2.10MB/s  0:00:01')['transferred_bytes']==1234
    event=progress_fields('ARCHIVE_PROGRESS {"detail":"解码预测视频","done":3,"total":12,"unit":"chunks"}')
    assert event['done']==3 and event['total']==12
    assert progress_fields('ordinary log')=={}


def test_progress_stream_preserves_stdout_and_reports_before_exit():
    from archive_progress import stream_call
    import time
    events=[];start=time.monotonic()
    output=stream_call([sys.executable,'-c','import time;print("first",flush=True);time.sleep(.3);print("last",flush=True)'],
                       lambda s:events.append((time.monotonic()-start,s)),timeout=5)
    assert output==b'first\nlast\n'
    assert events[0][1]=='first' and events[-1][1]=='last'
    assert events[-1][0]-events[0][0]>.2
