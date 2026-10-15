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
        assert '开始本条' in page and not called
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
