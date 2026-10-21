import json
from pathlib import Path
import numpy as np
import pytest
from evaluation.real_aloha.bread_eval import video_split, validate_bundle, digest, load_contract, action_metrics, observation, compare


def test_split_never_treats_tests_as_training():
    assert video_split('bread_004.mp4')=='train'
    assert video_split('bread_test000.mp4')=='test'
    assert video_split('bread_testxxx.mp4') is None
    assert video_split('human.mp4') is None


def test_bundle_rejects_leakage_and_modified_test(tmp_path):
    f=tmp_path/'test.mp4';f.write_bytes(b'test video')
    rows=[dict(id='bread_000',source='/source/bread_000.mp4',split='train',sha256=digest(f)),
          dict(id='bread_test000',source='/source/bread_test000.mp4',split='test',sha256=digest(f),bundle_path='test.mp4')]
    m=tmp_path/'manifest.json';m.write_text(json.dumps(dict(videos=rows)))
    with pytest.raises(ValueError,match='overlap'):validate_bundle(tmp_path)
    rows[0]['sha256']='other';m.write_text(json.dumps(dict(videos=rows)))
    assert len(validate_bundle(tmp_path)[1])==1
    f.write_bytes(b'changed')
    with pytest.raises(ValueError,match='modified'):validate_bundle(tmp_path)


def test_contract_requires_timing_stats_and_preprocessing(tmp_path):
    stats=tmp_path/'stats.json';stats.write_text('{}')
    c=dict(action_convention='episode_relative_physical_xyzw',model_actions_per_latent=8,
           model_action_dt_s=1/30,model_video_fps=15,stats_path='stats.json',stats_sha256=digest(stats),
           icl_fps=12,icl_height=320,icl_width=480,icl_rope_h=24,icl_guidance_scale=5,
           demo_preprocessing='server_native_bilinear_no_extra_rotation')
    p=tmp_path/'contract.json';p.write_text(json.dumps(c));assert load_contract(p)[1]==stats
    c['model_action_dt_s']=.05;p.write_text(json.dumps(c))
    with pytest.raises(ValueError,match='timing mismatch'):load_contract(p)
    c['model_action_dt_s']=1/30;c['stats_sha256']='bad';p.write_text(json.dumps(c))
    with pytest.raises(ValueError,match='SHA256'):load_contract(p)
    c['stats_sha256']=digest(stats);c['demo_preprocessing']=None;p.write_text(json.dumps(c))
    with pytest.raises(ValueError,match='preprocessing'):load_contract(p)


def test_action_uses_first_valid_frame_not_padded_frame():
    a=np.zeros((16,2,8));a[:,0,:]=100
    a[0,1,0]=.03;a[1,1,0]=.04;a[10,1,0]=-.12
    r=action_metrics(a,8)
    np.testing.assert_allclose(r['first_cm'],[5,12]);assert r['task_success'] is None
    with pytest.raises(ValueError):action_metrics(a[:14],8)
    a[0,1,0]=np.nan
    with pytest.raises(ValueError):action_metrics(a,8)


def test_observation_rejects_wrong_cameras_and_dtypes(tmp_path):
    p=tmp_path/'obs.npz';np.savez(p,head=np.zeros((4,4,3),dtype=np.uint8))
    with pytest.raises(ValueError,match='exactly'):observation(p)
    from evaluation.real_aloha.bread_eval import CAMERAS
    np.savez(p,**{k:np.zeros((4,4,3)) for k in CAMERAS})
    with pytest.raises(ValueError,match='uint8'):observation(p)


def test_compare_rejects_different_observation(tmp_path):
    a=tmp_path/'before';b=tmp_path/'after';a.mkdir();b.mkdir()
    for p,v in [(a,'old'),(b,'new')]:
        (p/'settings.json').write_text(json.dumps(dict(observation_sha256=v)))
    with pytest.raises(ValueError,match='observation_sha256'):compare(a,b,tmp_path/'report.json')
