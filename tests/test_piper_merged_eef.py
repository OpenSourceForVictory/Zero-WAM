"""Exercise the migrated loop with fakes; never import the hardware SDK."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time
import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R


def functions():
    source=Path('evaluation/real_aloha/run_piper_eef.py').read_text()
    names={'eef_initial','relative_eef_to_piper','measured_eef_action','_inference_loop','format_obs','model_joint_action_to_piper','send_action','checked_eef_feedback','send_eef_checked','limit_eef_command'}
    nodes=[x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name in names]
    ns=dict(np=np,R=R,Path=Path,json=json,time=NS(time=time.time,monotonic=time.monotonic,sleep=lambda _:None),
            InferenceStatus=NS,_check_emergency_stop=lambda:False,
            cv2=NS(COLOR_BGR2RGB=1,cvtColor=lambda a,b:a,imwrite=lambda *x:True))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<client-loop>','exec'),ns)
    return ns


class Piper:
    def __init__(self):
        self.poses=np.array([[.2,.1,.3,0,0,0],[.2,-.1,.3,0,0,0]])
        self.gripper_l=NS(get_gripper_status=lambda:NS(msg=NS(value=.04)))
        self.gripper_r=NS(get_gripper_status=lambda:NS(msg=NS(value=.04)))
        self.sent=[]
        self.status=0
        self.eef_guard=NS(validate=lambda p,current,target:{
            name:{'target_joints':np.asarray(target[i]).tolist(),
                  'joint_path':[np.asarray(current[i]).tolist(),np.asarray(target[i]).tolist()]}
            for i,name in enumerate(('left','right'))})
        self.stops=0
        def arm(i):
            return NS(get_arm_status=lambda:NS(msg=NS(arm_status=self.status),timestamp=time.time()),
                      get_flange_pose=lambda:NS(msg=self.poses[i].copy(),timestamp=time.time()),
                      get_joint_angles=lambda:NS(msg=self.poses[i].copy(),timestamp=time.time()),
                      move_j=lambda q:self.move_arm(i,q),
                      electronic_emergency_stop=self.stop)
        self.robot_l,self.robot_r=arm(0),arm(1)
    def stop(self):self.stops+=1
    def move_arm(self,i,q):
        self.poses[i]=q
        if i==1:self.sent.append(tuple(self.poses.copy()))
    def get_end_pose(self):return self.poses.copy()
    def get_joint_angles(self):return np.zeros(12)
    def send_end_pose_with_gripper(self,l,r,lg,rg):
        self.sent.append((l,r,lg,rg));self.poses=np.array([l,r])
    def send_end_pose(self,l,r):
        self.sent.append((l,r));self.poses=np.array([l,r])
    def send_joints_with_gripper(self,*args):
        self.sent.append(args);self.poses=np.asarray(args[:2])
    def send_joints_with_gripper_movejs(self,*args):self.sent.append(args)
    def wait_both_motion_done(self,**kw):return True


class Policy:
    def __init__(self,dim=16):self.calls=[];self.dim=dim
    def get_server_metadata(self):return dict(icl_ids=['bread_plate_20261008_v1'],executed_action_history=True)
    def infer(self,request):
        self.calls.append(request)
        if 'obs' in request and not request.get('compute_kv_cache'):
            a=np.zeros((self.dim,2,16))
            if self.dim==16:a[[6,14]]=1;a[[7,15]]=.5;a[[0,8]]=.001
            return {'action':a}
        return {}
    def compute_kv_cache(self,*args):pass


def test_continuous_execution_requires_measured_history_before_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(Policy, 'get_server_metadata', lambda self: {})
    def unexpected_inference(self, request):
        pytest.fail('Unsupported server must be rejected before reset or inference')
    monkeypatch.setattr(Policy, 'infer', unexpected_inference)
    with pytest.raises(RuntimeError, match='executed_action_history'):
        run(tmp_path)


def run(tmp_path,dim=16,stopping=False,max_eef_actions=0,hold_grippers=False):
    ns=functions();piper=Piper();policy=Policy(dim)
    cfg=NS(control_mode='movejs',prompt='plate and bread',action_dt=.02,wait_tol_deg=3,
           icl_id='bread_plate_20261008_v1',no_transform=True,tcp_offset=0,max_chunks=2,save_dir=str(tmp_path),
           max_eef_actions=max_eef_actions,hold_grippers=hold_grippers)
    logger=NS(save_dir=tmp_path,start_time=time.time(),log_server_infer=lambda *x:None,
              log_key_frame=lambda *x:None,log_kv_cache=lambda *x:None)
    camera=NS(get_frames=lambda:(np.zeros((2,2,3),np.uint8),)*3)
    if hold_grippers:
        piper.gripper_l.get_gripper_status=lambda:None
        piper.gripper_r.get_gripper_status=lambda:None
    if stopping:
        ns['_check_emergency_stop']=lambda:len(piper.sent)>=1
    count=ns['_inference_loop'](piper,camera,policy,cfg,logger,None)
    return count,piper,policy


def test_eef_loop_uses_initial_pose_and_measured_history(tmp_path):
    count,piper,policy=run(tmp_path)
    assert count==48 and len(piper.sent)==48
    assert piper.poses[0,0]==pytest.approx(.201)
    assert piper.sent[0][2:]==(.04,.04)
    assert policy.calls[0]['icl_id']=='bread_plate_20261008_v1'
    cache=[r for r in policy.calls if r.get('compute_kv_cache')]
    assert len(cache)==1 and len(cache[0]['obs'])==4
    assert cache[0]['executed_actions'].shape==(16,2,16)
    np.testing.assert_allclose(cache[0]['executed_actions'][0,1],.001)


@pytest.mark.parametrize('limit',[1,17,35])
def test_eef_action_limit_saves_only_executed_samples(tmp_path,limit):
    count,piper,policy=run(tmp_path,max_eef_actions=limit)
    assert count==limit and len(piper.sent)==limit
    partial=list(tmp_path.glob('eef_measured_partial_*.npy'))
    assert len(partial)==1
    samples=np.load(partial[0])
    assert samples.shape==(16,limit if limit<=16 else limit-16)
    np.testing.assert_allclose(samples[0],.001)
    assert len([r for r in policy.calls if r.get('compute_kv_cache')])==int(limit>16)


def test_single_arm_only_probe_needs_no_gripper_feedback(tmp_path):
    count,piper,policy=run(tmp_path,max_eef_actions=1,hold_grippers=True)
    assert count==1 and len(piper.sent[0])==2
    np.testing.assert_allclose(np.load(tmp_path/'eef_final_flange.npy'),piper.poses)
    assert not any(r.get('compute_kv_cache') for r in policy.calls)


def test_stop_is_checked_inside_eef_chunk(tmp_path):
    with pytest.raises(RuntimeError,match='Stop requested'):
        run(tmp_path,stopping=True)
    rows=[json.loads(x) for x in (tmp_path/'eef_commands.jsonl').read_text().splitlines()]
    assert len(rows)==1 and rows[0]['sent'] and not rows[0]['pose_reached']


@pytest.mark.parametrize('dim',[14,28])
def test_existing_joint_branches_still_run(tmp_path,dim):
    count,piper,policy=run(tmp_path,dim=dim)
    assert count==48 and len(piper.sent)==48


def test_eef_orientation_composition():
    ns=functions();initial=np.zeros(16);initial[[6,14]]=1
    initial[3:7]=R.from_euler('x',.4).as_quat()
    action=np.zeros(16);action[[6,14]]=1
    action[3:7]=R.from_euler('z',.2).as_quat()
    left,*_=ns['relative_eef_to_piper'](action,initial)
    expected=R.from_euler('x',.4)*R.from_euler('z',.2)
    assert (R.from_euler('xyz',left[3:])*expected.inv()).magnitude()<1e-10


def test_commissioning_limits_preserve_direction_and_bound_rotation():
    ns=functions();current=np.array([[.2,.1,.3,.4,.2,-.3],[.2,-.1,.3,0,0,0]])
    target=current.copy();target[0,:3]+=[.3,.4,0];target[1,0]+=.001
    start=R.from_euler('xyz',current[0,3:])
    target[0,3:]=(start*R.from_rotvec([0,0,.4])).as_euler('xyz')
    result=ns['limit_eef_command']((*target,.04,.05),current,.004,.025)
    np.testing.assert_allclose(result[0][:3]-current[0,:3],[.0024,.0032,0],atol=1e-12)
    np.testing.assert_allclose(result[1],target[1])
    rotation=start.inv()*R.from_euler('xyz',result[0][3:])
    np.testing.assert_allclose(rotation.as_rotvec(),[0,0,.025],atol=1e-12)
    assert result[2:]==(.04,.05)


def test_client_close_preserves_operator_stop(tmp_path):
    stop=tmp_path/'runtime_config.json';stop.write_text('{"emergency_stop": true}')
    class Legacy:
        def close(self):
            stop.write_text('{"emergency_stop": false}')
    source=ast.parse(Path('evaluation/real_aloha/run_piper_eef.py').read_text())
    node=next(x for x in source.body if isinstance(x,ast.ClassDef) and x.name=='WebsocketClientPolicy')
    ns={'_SiteWebsocketClientPolicy':Legacy}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<client>','exec'),ns)
    closed=[];client=ns['WebsocketClientPolicy']();client._ws=NS(close=lambda:closed.append(True))
    client.close();client.close()
    assert closed==[True] and json.loads(stop.read_text())['emergency_stop']


def test_controller_rejection_stops_after_first_send(tmp_path):
    ns=functions();piper=Piper()
    original=piper.send_joints_with_gripper
    def reject(*args):
        original(*args);piper.status=4
    piper.send_joints_with_gripper=reject
    with pytest.raises(RuntimeError,match='arm_status=4'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert len(piper.sent)==1 and piper.stops==2
    row=json.loads((tmp_path/'commands.jsonl').read_text())
    assert row['sent'] and not row['pose_reached']


def test_existing_fault_prevents_any_send(tmp_path):
    ns=functions();piper=Piper();piper.status=4
    with pytest.raises(RuntimeError,match='arm_status=4'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert not piper.sent


def test_kinematic_rejection_prevents_both_arm_commands(tmp_path):
    ns=functions();piper=Piper()
    def reject(*args):raise RuntimeError('right: no local IK')
    piper.eef_guard.validate=reject
    with pytest.raises(RuntimeError,match='no local IK'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert not piper.sent and piper.stops==0
    assert not json.loads((tmp_path/'commands.jsonl').read_text())['sent']


def test_joint_feedback_outside_plan_stops_both_arms(tmp_path):
    ns=functions();piper=Piper()
    original=piper.send_joints_with_gripper
    def bad_tracking(*args):
        original(*args)
        piper.robot_l.get_joint_angles=lambda:NS(msg=piper.poses[0]+.1,timestamp=time.time())
    piper.send_joints_with_gripper=bad_tracking
    with pytest.raises(RuntimeError,match='motion corridor'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert len(piper.sent)==1 and piper.stops==2


def test_missing_kinematic_guard_prevents_commands(tmp_path):
    ns=functions();piper=Piper();del piper.eef_guard
    with pytest.raises(RuntimeError,match='Missing controller-backed'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert not piper.sent


def test_teaching_mode_prevents_any_send(tmp_path):
    ns=functions();piper=Piper()
    piper.robot_l.get_arm_status=lambda:NS(msg=NS(arm_status=0,ctrl_mode=2),timestamp=time.time())
    with pytest.raises(RuntimeError,match='require CAN_CTRL'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert not piper.sent


def test_hold_grippers_sends_only_arm_targets(tmp_path):
    ns=functions();piper=Piper()
    target=piper.poses.copy();target[:,0]+=.004
    ns['send_eef_checked'](piper,(*target,.1,.1),tmp_path/'commands.jsonl',hold_grippers=True)
    assert len(piper.sent)==1 and len(piper.sent[0])==2
    row=json.loads((tmp_path/'commands.jsonl').read_text())
    assert row['pose_reached'] and row['gripper_commands']==0 and row['grippers_m'] is None


def test_unchanged_feedback_is_not_counted_as_execution(tmp_path):
    ns=functions();piper=Piper()
    piper.send_joints_with_gripper=lambda *args:piper.sent.append(args)
    target=piper.poses.copy();target[:,0]+=.1
    with pytest.raises(TimeoutError):
        ns['send_eef_checked'](piper,(*target,.04,.04),tmp_path/'commands.jsonl',timeout=.002)
    assert len(piper.sent)==1 and piper.stops==2


def test_stale_feedback_cannot_authorize_command(tmp_path):
    ns=functions();piper=Piper()
    piper.robot_l.get_flange_pose=lambda:NS(msg=piper.poses[0],timestamp=time.time()-10)
    with pytest.raises(RuntimeError,match='stale'):
        ns['send_eef_checked'](piper,(*piper.poses,.04,.04),tmp_path/'commands.jsonl')
    assert not piper.sent
