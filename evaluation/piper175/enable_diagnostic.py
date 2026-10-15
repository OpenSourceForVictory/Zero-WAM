import json,time
from pathlib import Path
from arm import channel_for,check_motion
from pyAgxArm import create_agx_arm_config,AgxArmFactory,ArmModel
out=Path('recovery_checks')/time.strftime('%Y%m%d_%H%M%S_enable_diagnostic')
out.mkdir()
a=AgxArmFactory.create_arm(create_agx_arm_config(robot=ArmModel.PIPER,firmeware_version='default',interface='socketcan',channel=channel_for('left')))
try:
 a.connect();time.sleep(.5);check_motion(a)
 print('log',str(out),flush=True)
 with (out/'feedback.jsonl').open('w') as f:
  def sample(stage):
   s=a.get_arm_status(); js=a.get_joint_angles()
   ds=[a.get_driver_states(i) for i in range(1,7)]
   r={'time':time.time(),'stage':stage,'status':str(s.msg) if s else None,'joint_rad':list(js.msg) if js else None,'enabled':a.get_joints_enable_status_list(),'drivers':[str(d.msg) if d else None for d in ds]}
   f.write(json.dumps(r)+'\n');f.flush();return r
  sample('before')
  print('single enable return',a.enable(),flush=True)
  end=time.monotonic()+5
  while time.monotonic()<end:
   sample('after');time.sleep(.05)
  r=sample('final');print(json.dumps(r),flush=True)
finally:a.disconnect()
