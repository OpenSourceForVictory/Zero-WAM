"""Exercise the migrated loop with fakes; never import the hardware SDK."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import time
import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R


def functions():
    source=Path('evaluation/real_aloha/run_piper_eef.py').read_text()
    names={'eef_initial','relative_eef_to_piper','measured_eef_action','_inference_loop','format_obs','model_joint_action_to_piper','send_action'}
    nodes=[x for x in ast.parse(source).body if isinstance(x,ast.FunctionDef) and x.name in names]
    ns=dict(np=np,R=R,Path=Path,time=NS(time=time.time,monotonic=time.monotonic,sleep=lambda _:None),
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
    def get_end_pose(self):return self.poses.copy()
    def get_joint_angles(self):return np.zeros(12)
    def send_end_pose_with_gripper(self,l,r,lg,rg):
        self.sent.append((l,r,lg,rg));self.poses=np.array([l,r])
    def send_joints_with_gripper(self,*args):self.sent.append(args)
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


def run(tmp_path,dim=16,stopping=False):
    ns=functions();piper=Piper();policy=Policy(dim)
    cfg=NS(control_mode='movejs',prompt='plate and bread',action_dt=.02,wait_tol_deg=3,
           icl_id='bread_plate_20261008_v1',no_transform=True,tcp_offset=0,max_chunks=2,save_dir=str(tmp_path))
    logger=NS(save_dir=tmp_path,start_time=time.time(),log_server_infer=lambda *x:None,
              log_key_frame=lambda *x:None,log_kv_cache=lambda *x:None)
    camera=NS(get_frames=lambda:(np.zeros((2,2,3),np.uint8),)*3)
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


def test_stop_is_checked_inside_eef_chunk(tmp_path):
    count,piper,policy=run(tmp_path,stopping=True)
    assert count==1 and len(piper.sent)==1
    assert not any(r.get('compute_kv_cache') for r in policy.calls)


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
