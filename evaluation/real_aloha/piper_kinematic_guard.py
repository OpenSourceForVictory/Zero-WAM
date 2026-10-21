"""Read-only PiPER kinematic preflight. Not a collision or dynamics validator.

Use the installed SDK's MDH and limits queried from each controller. A local
solution is required; we never search distant IK branches or relax limits.
"""
import time
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as R


class KinematicRejection(RuntimeError):
    pass


class PiperKinematics:
    def __init__(self, mdh, limits, joint_margin=.01, max_joint_step=.05):
        self.mdh = np.asarray(mdh, float)
        self.limits = np.asarray(limits, float)
        self.margin, self.max_step = joint_margin, max_joint_step
        if (self.mdh.shape != (6, 4) or self.limits.shape != (6, 2)
                or not np.isfinite(self.mdh).all() or not np.isfinite(self.limits).all()
                or np.any(self.limits[:, 1]-self.limits[:, 0] <= 2*joint_margin)):
            raise ValueError('Invalid MDH or controller joint limits')

    def fk(self, q):
        q = np.asarray(q, float)
        if q.shape != (6,) or not np.isfinite(q).all():
            raise ValueError('Expected six finite joint angles in radians')
        t = np.eye(4)
        for qi, (d, a, alpha, offset) in zip(q, self.mdh):
            c, s = np.cos(qi+offset), np.sin(qi+offset)
            ca, sa = np.cos(alpha), np.sin(alpha)
            t = t @ np.array([[c,-s,0,a], [ca*s,ca*c,-sa,-sa*d],
                              [sa*s,sa*c,ca,ca*d], [0,0,0,1]])
        return t

    def conditioning(self, q):
        t = self.fk(q)
        jac = np.zeros((6, 6))
        for i in range(6):
            shifted = np.array(q, float); shifted[i] += 1e-6
            nxt = self.fk(shifted)
            jac[:3, i] = (nxt[:3, 3]-t[:3, 3])/(1e-6*.3)
            jac[3:, i] = R.from_matrix(nxt[:3,:3]@t[:3,:3].T).as_rotvec()/1e-6
        return float(np.linalg.svd(jac, compute_uv=False)[-1])

    def check_start(self, q):
        q = np.asarray(q, float)
        self.fk(q)  # Validate before broadcasting.
        clearance = np.minimum(q-self.limits[:,0], self.limits[:,1]-q)
        bad = np.flatnonzero(clearance < self.margin)
        if len(bad):
            raise KinematicRejection('Joint boundary: ' + ', '.join(
                f'J{i+1} clearance={np.degrees(clearance[i]):.3f}deg' for i in bad))
        sigma = self.conditioning(q)
        if sigma < .015:
            raise KinematicRejection(f'Near singular start: normalized sigma_min={sigma:.6f}')
        return sigma

    def plan(self, q, measured_flange, target_flange):
        q = np.asarray(q, float)
        measured, target = np.asarray(measured_flange, float), np.asarray(target_flange, float)
        if measured.shape != (6,) or target.shape != (6,) or not np.isfinite([measured,target]).all():
            raise KinematicRejection('Invalid flange pose')
        sigma = self.check_start(q)
        start = self.fk(q)
        if (np.linalg.norm(start[:3,3]-measured[:3]) > .002 or
                (R.from_matrix(start[:3,:3]).inv()*R.from_euler('xyz',measured[3:])).magnitude() > .02):
            raise KinematicRejection('SDK FK disagrees with flange feedback')
        start_r = R.from_matrix(start[:3,:3])
        goal_r = R.from_euler('xyz',target[3:])
        delta_r = (start_r.inv()*goal_r).as_rotvec()
        distance = np.linalg.norm(target[:3]-start[:3,3])
        angle = np.linalg.norm(delta_r)
        if distance > .020 or angle > .10:
            raise KinematicRejection('Target exceeds local planning radius (20mm / 0.1rad)')
        count = max(1,int(np.ceil(distance/.001)),int(np.ceil(angle/.01)))
        low = np.maximum(self.limits[:,0]+self.margin, q-self.max_step)
        high = np.minimum(self.limits[:,1]-self.margin, q+self.max_step)
        path, previous = [q.copy()], q.copy()
        max_pos, max_rot = 0., 0.
        for fraction in np.linspace(0,1,count+1)[1:]:
            pos = start[:3,3]+fraction*(target[:3]-start[:3,3])
            rot = start_r*R.from_rotvec(fraction*delta_r)
            def error(candidate):
                t = self.fk(candidate)
                return np.r_[(t[:3,3]-pos)/.3,
                             (R.from_matrix(t[:3,:3]).inv()*rot).as_rotvec()]
            solution = least_squares(error,previous,bounds=(low,high),max_nfev=120,
                                     ftol=1e-10,xtol=1e-10,gtol=1e-10)
            t = self.fk(solution.x)
            pe = float(np.linalg.norm(t[:3,3]-pos))
            re = float((R.from_matrix(t[:3,:3]).inv()*rot).magnitude())
            if not solution.success or pe > .0005 or re > .005:
                raise KinematicRejection(f'No local IK within joint-step budget: position={pe:.6f}m rotation={re:.6f}rad')
            self.check_start(solution.x)
            path.append(solution.x.copy());previous=solution.x
            max_pos,max_rot=max(max_pos,pe),max(max_rot,re)
        # Check the joint-linear path too; MOVE_P is not guaranteed to follow this path.
        for fraction in np.linspace(0,1,21):
            self.check_start(q+fraction*(previous-q))
        return dict(joint_path=np.asarray(path).tolist(),target_joints=previous.tolist(),
                    max_joint_change_rad=float(np.max(np.abs(previous-q))),
                    position_error_m=max_pos,rotation_error_rad=max_rot,
                    initial_sigma_min=sigma,collision_checked=False,
                    controller_path_verified=False)


class LivePiperGuard:
    """Controller-backed check for BOTH arms before either command is sent."""
    def __init__(self, piper):
        from pyAgxArm.api.constants import ROBOT_MDH_PRESET
        self.models = []
        for arm in (piper.robot_l,piper.robot_r):
            limits=[]
            for i in range(1,7):
                sample=arm.get_joint_angle_vel_limits(i,timeout=1.)
                if sample is None:
                    raise KinematicRejection(f'Missing controller joint limit J{i}')
                limits.append([sample.msg.min_angle_limit,sample.msg.max_angle_limit])
            self.models.append(PiperKinematics(ROBOT_MDH_PRESET['piper'],limits))

    @staticmethod
    def joints(arm):
        joint=arm.get_joint_angles()
        if joint is None or not -.1 <= time.time()-joint.timestamp <= .5:
            raise KinematicRejection('Missing/stale joint feedback')
        return np.asarray(joint.msg,float)

    def check_start(self,piper):
        for name,model,arm in zip(('left','right'),self.models,(piper.robot_l,piper.robot_r)):
            try:model.check_start(self.joints(arm))
            except KinematicRejection as exc:raise KinematicRejection(f'{name}: {exc}') from exc

    def validate(self,piper,current,target):
        plans={}
        for i,(name,model,arm) in enumerate(zip(('left','right'),self.models,(piper.robot_l,piper.robot_r))):
            q=self.joints(arm)
            try:plans[name]=model.plan(q,current[i],target[i])
            except KinematicRejection as exc:raise KinematicRejection(f'{name}: {exc}') from exc
        return plans
