import numpy as np
import pytest
from scipy.spatial.transform import Rotation as R
from evaluation.real_aloha.piper_kinematic_guard import PiperKinematics,KinematicRejection

MDH=[[.123,0,0,0],[0,0,-np.pi/2,-3.0058060377846343],
     [0,.28503,0,-1.793849405199772],[.25075,-.02198,np.pi/2,0],
     [0,0,-np.pi/2,0],[.091,0,np.pi/2,0]]
LIMITS=np.deg2rad([[-150,150],[0,180],[-170,0],[-100,100],[-70,70],[-180,180]])

def model():return PiperKinematics(MDH,LIMITS)
def pose(t):return np.r_[t[:3,3],R.from_matrix(t[:3,:3]).as_euler('xyz')]

def test_failed_physical_start_rejected_before_ik():
    q=[0,.0002443460952792061,.0009075712110370515,0,.015620696805349249,0]
    m=model()
    target=[.0583665833,.00328442074,.2125093273,-.331599,1.49635457,-.32452969]
    with pytest.raises(KinematicRejection,match='Joint boundary'):
        m.plan(q,pose(m.fk(q)),target)

def test_wrist_singularity_rejected_away_from_other_joint_limits():
    with pytest.raises(KinematicRejection,match='Near singular'):
        model().check_start([0,.8,-.8,0,0,0])

def test_local_reachable_target_and_joint_path():
    m=model();q=np.array([.1,.8,-.8,.2,.5,.1]);goal=q+np.array([.003,.004,-.003,.002,.003,.002])
    plan=m.plan(q,pose(m.fk(q)),pose(m.fk(goal)))
    assert plan['position_error_m']<.0005 and plan['rotation_error_rad']<.005
    path=np.asarray(plan['joint_path'])
    assert np.max(np.abs(path-q))<=.05+1e-10
    assert np.all(path>LIMITS[:,0]) and np.all(path<LIMITS[:,1])
    assert plan['collision_checked'] is False and plan['controller_path_verified'] is False

def test_fk_mismatch_rejected():
    m=model();q=[.1,.8,-.8,.2,.5,.1];measured=pose(m.fk(q));measured[0]+=.01
    with pytest.raises(KinematicRejection,match='FK disagrees'):
        m.plan(q,measured,measured)

def test_far_target_not_searched_with_random_branches():
    m=model();q=[.1,.8,-.8,.2,.5,.1];start=pose(m.fk(q));target=start.copy();target[0]+=.1
    with pytest.raises(KinematicRejection,match='planning radius'):
        m.plan(q,start,target)

def test_close_cartesian_target_cannot_force_large_joint_change():
    m=PiperKinematics(MDH,LIMITS,max_joint_step=.001)
    q=np.array([.1,.8,-.8,.2,.5,.1]);goal=q.copy();goal[0]+=.02
    with pytest.raises(KinematicRejection,match='No local IK'):
        m.plan(q,pose(m.fk(q)),pose(m.fk(goal)))
