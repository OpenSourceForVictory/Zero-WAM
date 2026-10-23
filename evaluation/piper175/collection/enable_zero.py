"""Exclusive low-speed joint-zero operation for the collection console."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT.parent
sys.path.insert(0, str(RUNTIME))


def check_flags(message):
    flags = ('voltage_too_low', 'motor_overheating', 'driver_overcurrent',
             'driver_overheating', 'sensor_status', 'driver_error_status',
             'collision_status', 'stall_status')
    for flag in flags:
        value = re.search(r'\b' + flag + r'\s*:\s*(True|False)', str(message))
        if value and value[1] == 'True':
            raise RuntimeError('当前硬件故障：' + flag)


def check_arm(side):
    from arm import channel_for, check_motion
    from piper_sdk import C_PiperInterface
    from pyAgxArm import create_agx_arm_config, AgxArmFactory, ArmModel
    grip = C_PiperInterface(can_name=channel_for(side))
    grip.ConnectPort(piper_init=False)
    try:
        time.sleep(.6)
        msg = grip.GetArmGripperMsgs()
        if not 0 <= time.time() - msg.time_stamp < .3:
            raise RuntimeError(side + '：夹爪反馈过期')
        check_flags(msg)
    finally:
        grip.DisconnectPort()
    arm = AgxArmFactory.create_arm(create_agx_arm_config(
        robot=ArmModel.PIPER, firmeware_version='default',
        interface='socketcan', channel=channel_for(side)))
    arm.connect()
    try:
        time.sleep(.6)
        check_motion(arm)
        for joint in range(1, 7):
            msg = arm.get_driver_states(joint)
            if msg is None or not 0 <= time.time() - msg.timestamp < .3:
                raise RuntimeError(side + '：关节反馈过期')
            check_flags(msg.msg)
    finally:
        arm.disconnect()


def controllers():
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc/'cmdline').read_bytes().split(b'\0')
            scripts = {Path(x.decode()).name for x in argv if x}
            if scripts & {'switch_control.py', 'run_live.py', 'real_robot_inference_merged.py', 'go_zero.py', 'recover_control.py'}:
                found.append((int(proc.name), scripts, (proc/'cwd').resolve()))
        except (OSError, UnicodeError):
            continue
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arm', choices=['left', 'right', 'both'], required=True)
    parser.add_argument('--speed-percent', type=int, choices=range(1, 7), default=1)
    args = parser.parse_args()
    with (RUNTIME/'.recover_and_zero.lock').open('a') as lock, (ROOT/'.relocalize.lock').open('a') as localization_lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(localization_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('停止遥操跟随和控制进程', flush=True)
        active = controllers()
        for pid, scripts, cwd in active:
            if 'switch_control.py' not in scripts or cwd != ROOT:
                raise RuntimeError('另有控制程序运行，拒绝并发归零：' + str(pid))
        for pid, _, _ in active:
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 12
        while controllers() and time.monotonic() < deadline:
            time.sleep(.2)
        if controllers():
            raise RuntimeError('旧控制程序未退出，未发送归零命令')
        sides = ['left', 'right'] if args.arm == 'both' else [args.arm]
        for side in sides:
            print('检查关节和夹爪故障：' + side, flush=True)
            check_arm(side)
        dest = RUNTIME/'recovery_checks'/time.strftime('%Y%m%d_%H%M%S_web_zero')
        dest.mkdir(parents=True, exist_ok=False)
        def operate(action):
            subprocess.run(['bash', str(RUNTIME/'inference_client/operate.sh'), action], cwd=RUNTIME, check=True)
        try:
            operate('resume')
            for side in sides:
                check_arm(side)
                print('正在使能并归零（' + str(args.speed_percent) + '%速度）：' + side, flush=True)
                subprocess.run([sys.executable, '-u', str(RUNTIME/'go_zero.py'), '--arm', side,
                                '--execute', '--speed-percent', str(args.speed_percent),
                                '--output', str(dest/side)], cwd=RUNTIME, check=True)
                result = json.loads((dest/side/'result.json').read_text())
                if not result.get('reached'):
                    raise RuntimeError(side + '未确认到位')
                print('归零完成：' + side + '；关节角：' + str(result['final_deg']), flush=True)
        finally:
            operate('stop')
        print('归零全部完成；未恢复遥操跟随；结果：' + str(dest), flush=True)


if __name__ == '__main__':
    main()
