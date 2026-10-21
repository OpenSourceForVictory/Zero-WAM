import copy,json
from pathlib import Path
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from evaluation.real_aloha.piper_contract import PiperContract,ActionQuantiles,check_timestamps
from evaluation.real_aloha.piper_capture import collect_commands
from evaluation.real_aloha.piper_server import configure

PROFILE=Path(__file__).resolve().parents[1]/'evaluation/real_aloha/profiles/piper_site_candidate.json'


def test_physical_pose_roundtrip_with_rotated_bases_and_nonzero_tcp():
    cfg=json.loads(PROFILE.read_text())
    for i in range(2):
        cfg['task_from_base'][i]=np.eye(4).tolist()
        m=np.eye(4);m[:3,:3]=Rotation.from_euler('xyz',[.2,.1,1.1+i]).as_matrix();m[:3,3]=[.1,i*.5,.3]
        cfg['task_from_base'][i]=m.tolist()
    c=PiperContract(cfg)
    initial=np.array([[.3,.1,.2,.2,.3,.1],[.4,-.1,.3,-.2,.1,.4]])
    target=initial+np.array([[.01,-.02,.03,.1,-.2,.1],[-.02,.01,.04,-.1,.1,.2]])
    stats=ActionQuantiles(np.full(16,-.4),np.full(16,.7),{})
    a=c.encode(initial,target,[.02,.07]);decoded,g=c.decode(initial,stats.denormalize(stats.normalize(a)))
    np.testing.assert_allclose(decoded[:,:3],target[:,:3],atol=1e-12)
    assert np.max((Rotation.from_euler('xyz',decoded[:,3:]).inv()*Rotation.from_euler('xyz',target[:,3:])).magnitude())<1e-12
    np.testing.assert_allclose(g,[.02,.07],atol=1e-12)


def test_known_rotation_and_fixed_episode_reference():
    cfg=json.loads(PROFILE.read_text());cfg['task_from_base']=[np.eye(4).tolist()]*2;cfg['flange_from_tcp']=[np.eye(4).tolist()]*2
    c=PiperContract(cfg);initial=np.zeros((2,6));a=np.zeros((2,8));a[:,6]=1;a[0,:3]=[.1,0,0];a[0,3:7]=Rotation.from_euler('z',.2).as_quat()
    pose,_=c.decode(initial,a.ravel());np.testing.assert_allclose(pose[0],[.1,0,0,0,0,.2],atol=1e-12)
    pose2,_=c.decode(initial,a.ravel());np.testing.assert_array_equal(pose,pose2)


@pytest.mark.parametrize('stamps,previous',[({'a':9.,'b':9.99},{}),({'a':10.1},{}),({'a':9.95},{'a':9.95}),({'a':9.8,'b':9.99},{})])
def test_reject_stale_future_repeated_skewed(stamps,previous):
    with pytest.raises(ValueError):check_timestamps(stamps,10.,previous,.25,.08)


def test_no_implicit_server_stats():
    with pytest.raises(ValueError,match='unset'):configure({},PROFILE)
    cfg=json.loads(PROFILE.read_text());cfg['action_convention']='robotwin_eval'
    with pytest.raises(ValueError,match='physical'):PiperContract(cfg)


def test_command_labels_require_send_timestamp(tmp_path):
    p=tmp_path/'commands.jsonl';pose=np.zeros((2,6)).tolist()
    p.write_text('\n'.join(json.dumps(r) for r in [{'sent':False},{'sent':True,'target_flange':pose},{'sent':True,'sent_at':10.,'target_flange':pose,'grippers_m':None}]))
    rows,errors=collect_commands(p,9,11)
    assert len(rows)==1 and rows[0]['grippers_m'] is None and len(errors)==1
    assert collect_commands(None,9,11)==([],[])


def test_server_profile_explicit_stats_and_timing(tmp_path):
    import hashlib
    cfg=json.loads(PROFILE.read_text())
    data={'method':'abs','norm_stats':{'action.hand.position':{'q01':[-1]*14,'q99':[1]*14},'action.effector.position':{'q01':[0,0],'q99':[1,1]}}}
    raw=json.dumps(data).encode();(tmp_path/'stats.json').write_bytes(raw)
    cfg.update(normalization={'path':'stats.json','sha256':hashlib.sha256(raw).hexdigest(),'action_convention':cfg['action_convention']},model_action_dt_s=1/30,model_actions_per_latent=8,model_video_fps=15)
    path=tmp_path/'profile.json';path.write_text(json.dumps(cfg));base={'norm_stat':{'old':True},'action_per_frame':16}
    result=configure(base,path)
    assert result['action_per_frame']==8 and result['norm_stat']['q01'][28]==0
    assert base['norm_stat']=={'old':True} and base['action_per_frame']==16
    cfg['model_actions_per_latent']=16;path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='Timing mismatch'):configure(base,path)
    cfg['normalization']['sha256']='wrong';path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='SHA256'):configure(base,path)


def test_audit_recorded_episode_with_explicit_command_and_tamper(tmp_path):
    from PIL import Image
    from evaluation.real_aloha.piper_audit import audit
    from evaluation.real_aloha.piper_contract import CAMERAS
    cfg=json.loads(PROFILE.read_text());c=PiperContract(cfg)
    (tmp_path/'profile.json').write_text(json.dumps(cfg))
    (tmp_path/'capture.json').write_text(json.dumps({'profile_sha256':c.digest,'complete':True}))
    rows=[]
    for i in range(3):
        folder=tmp_path/f'frame_{i:06d}';folder.mkdir()
        for cam in CAMERAS:Image.new('RGB',(8,8)).save(folder/(cam+'.png'))
        pose=np.zeros((2,6));pose[:,0]=.1+i*.001
        stamps={key:10+i*.1 for key in list(CAMERAS)+[f'arm{j}.{k}' for j in range(2) for k in ('flange','joints','gripper','status')]}
        rows.append({'time':10+i*.1+.001,'timestamps':stamps,'flange_m_rad':pose.tolist(),'grippers_m':[0,.08],'arm_status':[0,0],'image_directory':folder.name})
    (tmp_path/'samples.jsonl').write_text('\n'.join(map(json.dumps,rows)))
    cmd={'sent':True,'sent_at':10.05,'completed_at':10.1,'target_flange':rows[1]['flange_m_rad'],'grippers_m':[0,.08],'pose_reached':True}
    (tmp_path/'commands.jsonl').write_text(json.dumps(cmd))
    report=audit(tmp_path)
    assert report['roundtrip_passed'] and report['control_targets_available']
    assert not report['normalization_checked'] and not report['deployment_validated']
    np.testing.assert_allclose(report['commands'][0]['post_completion_position_error_m'],0)
    cfg['flange_from_tcp'][0][2][3]+=.01;(tmp_path/'profile.json').write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='digest'):audit(tmp_path)
