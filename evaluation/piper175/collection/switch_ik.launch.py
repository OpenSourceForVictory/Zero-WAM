"""Isolated legacy FK/IK/relative-pose frontend; no CAN drivers."""
import os
import sys
from launch import LaunchDescription
from launch.actions import EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch_ros.actions import Node


def generate_launch_description():
    index=os.environ['PIKA_SWITCH_INDEX']
    nodes=[]
    for script,name in [('piper_FK.py','piper_FK'),('piper_IK.py','piper_IK'),
                        ('teleop_piper_publish.py','teleop_piper')]:
        nodes.append(Node(package='pika_remote_piper',executable=script,
                          name=name+index,prefix=sys.executable,output='screen',
                          parameters=[{'index_name':index}],
                          arguments=['--return_zero_position','False'] if name=='teleop_piper' else [],
                          remappings=[('/pika_pose'+index,'/pika_pose_l'),
                                      ('/joint_states_single'+index,'/pika_switch/source_feedback'),
                                      ('/joint_states'+index,'/pika_switch/ik_target'),
                                      ('/teleop_status'+index,'/pika_switch/frontend_status')]))
    return LaunchDescription(nodes+[RegisterEventHandler(OnProcessExit(target_action=n,on_exit=[
        EmitEvent(event=Shutdown(reason='FK/IK frontend exited'))])) for n in nodes])
