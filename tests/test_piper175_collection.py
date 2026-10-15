"""Offline checks of dataset validity, timing and action/state separation."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]/'evaluation/piper175/collection'
spec=importlib.util.spec_from_file_location('pika_collection_core',ROOT/'core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)


def profile(): return core.load_profile(ROOT/'profile.json')


def image_payload(stamps,serials=None):
    buf=io.BytesIO();p=profile()
    np.savez(buf,high=np.zeros((480,640,3),np.uint8),left=np.ones((480,640,3),np.uint8),
             right=np.full((480,640,3),2,np.uint8),timestamps=stamps,
             serials=serials or p['camera_serials'])
    return buf.getvalue()


def test_role_order_and_lossless_images():
    frames,_=core.decode_cameras(image_payload([9.99,9.98,9.97]),profile(),10)
    assert [int(frames[k][0,0,0]) for k in profile()['camera_roles']]==[0,1,2]


@pytest.mark.parametrize('stamps',[[8,9.98,9.97],[10.1]*3,[9.99,9.8,9.99],[float('nan')]*3])
def test_bad_cameras_rejected(stamps):
    with pytest.raises(ValueError): core.decode_cameras(image_payload(stamps),profile(),10)


def test_serial_mismatch():
    with pytest.raises(ValueError):
        core.decode_cameras(image_payload([9.99]*3,['wrong']*3),profile(),10)


def row(stream,t,value=1):
    d={'position':[value]*6+[.02]}
    if stream.startswith('pika_'):
        d={'pose':{'position':dict(x=0,y=0,z=0),'orientation':dict(x=0,y=0,z=0,w=1)}}
    return {'stream':stream,'source_time':t,'receive_time':t+.001,'message':d}


def test_no_future_action_and_no_stale_header():
    old=row('action_left',10);future=row('action_left',10.02,2)
    assert core.select_sample([old,future],10.01,.1)==old
    old['receive_time']=11
    assert core.select_sample([old,future],10.01,.1) is None


def test_invalid_numeric_target():
    bad=row('action_left',10,float('nan'))
    assert core.select_sample([bad],10.01,.1) is None


def test_sense_error_blocks_readiness():
    r={'stream':'sense_left','message':{'error':True,'angle':1.67,'distance':.0967}}
    assert not core.valid_message(r)
    r['message']['error']=False
    assert core.valid_message(r)
    r['message']['angle']=float('nan')
    assert not core.valid_message(r)


@pytest.fixture
def dataset(tmp_path):
    p=profile(); (tmp_path/'frames').mkdir()
    (tmp_path/'manifest.json').write_text(json.dumps({'profile':p,'status':'complete'}))
    messages=[]; frames=[]
    for i in range(12):
        t=10+i*.1
        for key in p['required_topics']:
            messages.append(row(key,t-.01,2 if key.startswith('action_') else 1))
        frames.append({'file':f'frames/{i:06}.npz','source_times':[t]*3,'receive_time':t+.01})
        (tmp_path/f'frames/{i:06}.npz').write_bytes(image_payload([t]*3))
    (tmp_path/'messages.jsonl').write_text('\n'.join(json.dumps(x) for x in messages))
    (tmp_path/'frames.jsonl').write_text('\n'.join(json.dumps(x) for x in frames))
    return tmp_path


def test_complete_episode_keeps_actions_separate(dataset):
    rows,quality=core.build_index(dataset)
    assert quality['valid'] and quality['aligned']==12
    assert rows[0]['state']==[1]*12+[.02,.02]
    assert rows[0]['action']==[2]*12+[.02,.02]


def test_single_left_export_is_seven_dimensional(dataset,monkeypatch):
    manifest=dataset/'manifest.json'
    meta=json.loads(manifest.read_text())
    meta['profile']['active_arms']=['left']
    meta['profile']['required_topics']={k:v for k,v in meta['profile']['required_topics'].items() if k.endswith('_left')}
    manifest.write_text(json.dumps(meta))
    rows,quality=core.build_index(dataset)
    assert quality['valid'] and rows[0]['action']==[2]*6+[.02]
    monkeypatch.syspath_prepend(str(ROOT))
    spec=importlib.util.spec_from_file_location('single_cli',ROOT/'collect.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    cli.export(dataset)
    import h5py
    with h5py.File(dataset/'data.hdf5') as f:
        assert f['action'].shape==(12,7)
        assert 'left' in f.attrs['vector_order']


def test_partial_episode_not_exportable(dataset):
    p=dataset/'manifest.json';d=json.loads(p.read_text());d['status']='failed';p.write_text(json.dumps(d))
    assert not core.build_index(dataset)[1]['valid']


def test_missing_stream_is_invalid(dataset):
    p=dataset/'messages.jsonl'
    p.write_text('\n'.join(s for s in p.read_text().splitlines() if json.loads(s)['stream']!='state_left'))
    assert not core.build_index(dataset)[1]['valid']


def test_hdf5_export_and_no_overwrite(dataset,monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec=importlib.util.spec_from_file_location('pika_collection_cli',ROOT/'collect.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    cli.export(dataset)
    import h5py
    with h5py.File(dataset/'data.hdf5') as f:
        assert f['action'].shape==(12,14)
        assert f['action'][0,0]==2 and f['observations/qpos'][0,0]==1
    with pytest.raises(FileExistsError): cli.export(dataset)


def test_corrupt_archive_rejected(dataset,monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec=importlib.util.spec_from_file_location('pika_collection_cli_bad',ROOT/'collect.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    (dataset/'frames/000000.npz').write_bytes(b'broken')
    with pytest.raises(ValueError): cli.validate(dataset)


def test_repeated_camera_timestamp_invalidates_episode(dataset):
    p=dataset/'frames.jsonl';rows=[json.loads(s) for s in p.read_text().splitlines()]
    rows[1]['source_times']=rows[0]['source_times']
    p.write_text('\n'.join(json.dumps(x) for x in rows))
    assert not core.build_index(dataset)[1]['valid']
