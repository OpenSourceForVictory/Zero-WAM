"""Separate acquisition sensor nodes from explicitly requested robot control."""
import json
import os
import sys
from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, RegisterEventHandler, EmitEvent
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def setup(context):
    p=json.loads(Path(os.environ['PIKA175_PROFILE']).read_text())
    mode=LaunchConfiguration('mode').perform(context)
    nodes=[]
    if mode=='sensors':
        nodes.append(Node(package='pika_locator',executable='pika_double_locator_node',
                          name='pika_double_locator',output='screen',parameters=[{
                              'left_hand_code':p['tracker_codes'][0],
                              'right_hand_code':p['tracker_codes'][1],
                              'publish_rate':100.0,'dist_limit':0.2,'angle_limit':0.2,
                              'linear_limit':5.0,'angular_limit':20.0}]))
        for i,side in enumerate(('l','r')):
            if ('left','right')[i] not in p.get('sense_arms',p.get('active_arms',['left','right'])): continue
            remaps=[('/imu/data',f'/sensor/imu_{side}/data'),
                    ('/gripper/data',f'/sensor/gripper_{side}/data'),
                    ('/gripper/ctrl',f'/sensor/gripper_{side}/ctrl'),
                    ('/gripper/joint_state',f'/sensor/gripper_{side}/joint_state'),
                    ('/gripper/joint_state_ctrl',f'/sensor/joint_states_{side}'),
                    ('/joint_state_info',f'/joint_states_{side}'),
                    ('/joint_state_gripper',f'/joint_states_gripper_{side}'),
                    ('/teleop_trigger',f'/teleop_trigger_{side}'),
                    ('/data_capture_status','/data_tools_dataCapture/status'),
                    ('/teleop_status',f'/teleop_status_{side}'),
                    ('/localization_status',f'/pika_localization_status_{side}'),
                    ('/arm_control_status',f'/arm_control_status_{side}')]
            nodes.append(Node(package='sensor_tools',executable='serial_gripper_imu',
                              name=f'sensor_serial_gripper_imu_{side}',output='screen',
                              parameters=[{'serial_port':str(Path(p['serial_ports'][i]).resolve()),
                                           'joint_name':f'gripper_{side}_center_joint'}],remappings=remaps))
    elif mode=='teleop':
        can={}
        for word,port in zip(('left','right'),p['can_usb_ports']):
            if word not in p.get('active_arms',['left','right']): continue
            matches=[x.name for x in Path('/sys/class/net').iterdir() if (x/'device').resolve().name==port]
            if len(matches)!=1: raise RuntimeError(f'CAN mapping failed: {port}')
            can[word]=matches[0]
        for i,(side,word) in enumerate((('l','left'),('r','right'))):
            if word not in p.get('active_arms',['left','right']): continue
            nodes.append(Node(package='piper',executable='piper_single_ctrl',
                              name=f'piper_{word}_ctrl_node',output='screen',prefix=sys.executable,
                              parameters=[{'can_port':can[word],'auto_enable':True,
                                           'rviz_ctrl_flag':False,'girpper_exist':True,'piper_name':word}],
                              remappings=[('pos_cmd',f'/pos_{word}_cmd'),
                                          (f'/puppet/joint_{word}',f'/joint_states_single_{side}'),
                                          ('joint_ctrl_single',f'/joint_states_gripper_{side}')]))
            for script,name in [('piper_FK.py','piper_FK'),('piper_IK.py','piper_IK'),
                                ('teleop_piper_publish.py','teleop_piper')]:
                nodes.append(Node(package='pika_remote_piper',executable=script,
                                  name=f'{name}_{side}',output='screen',prefix=sys.executable,
                                  parameters=[{'index_name':f'_{side}'}],
                                  arguments=['--return_zero_position','False'] if name=='teleop_piper' else []))
    else:
        raise ValueError('Unknown mode')
    # Do not respawn a controller following failure. A process exit stops this launch group.
    handlers=[RegisterEventHandler(OnProcessExit(target_action=n,on_exit=[
        EmitEvent(event=Shutdown(reason='A collection dependency exited; no automatic restart'))])) for n in nodes]
    return handlers+nodes


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('mode'),OpaqueFunction(function=setup)])
