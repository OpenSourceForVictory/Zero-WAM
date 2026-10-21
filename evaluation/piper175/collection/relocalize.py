"""2026-10-16 localization recovery. No arm enable, zero or gripper commands."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / '.relocalize_status.json'
SERVICE = 'pika-collection-sensors.service'


def write_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def config_valid(text):
    return len(re.findall(r'"PositionSet"\s*:\s*"1"', text)) == 2 and len(re.findall(r'"OOTXSet"\s*:\s*"1"', text)) == 2


def pose_metrics(rows, seconds=5):
    import numpy as np
    result = {'samples':len(rows), 'seconds':seconds, 'output_ok':False, 'static_ok':False}
    if len(rows) < 2:
        return result
    a = np.asarray(rows, dtype=float)
    if not np.isfinite(a).all():
        return result
    q = a[:,5:9]
    norm = np.linalg.norm(q,axis=1)
    if np.any(abs(norm-1) > .05):
        return result
    q = q/norm[:,None]
    result.update(range_mm=(np.ptp(a[:,2:5],axis=0)*1000).tolist(),
                  max_step_mm=float(np.linalg.norm(np.diff(a[:,2:5],axis=0),axis=1).max()*1000),
                  angle_deg=float(np.degrees(2*np.arccos(np.clip(abs(q@q[0]),0,1))).max()),
                  max_gap_s=float(np.diff(a[:,0]).max()), max_age_s=float((a[:,0]-a[:,1]).max()))
    result['output_ok'] = bool(len(rows) >= seconds*80 and result['max_gap_s'] < .2 and
                               np.all((a[:,0]-a[:,1] >= -.02) & (a[:,0]-a[:,1] < .2)))
    # Operational static screening, not a manufacturer's accuracy specification.
    result['static_ok'] = bool(result['output_ok'] and max(result['range_mm']) <= 2 and
                               result['angle_deg'] <= 1 and result['max_step_mm'] <= 1)
    return result


def verify(dest, prefix):
    import rclpy
    from geometry_msgs.msg import PoseStamped
    from rclpy.qos import qos_profile_sensor_data
    rclpy.init(); node = rclpy.create_node('pika_relocalization_check'); rows = []
    def receive(m):
        p=m.pose.position; q=m.pose.orientation
        rows.append([time.time(),m.header.stamp.sec+m.header.stamp.nanosec/1e9,p.x,p.y,p.z,q.x,q.y,q.z,q.w])
    node.create_subscription(PoseStamped, '/pika_pose_l', receive, qos_profile_sensor_data)
    results=[]
    try:
        for label, seconds in [('warmup',20),('static_1',5),('static_2',5)]:
            rows.clear(); deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                rclpy.spin_once(node,timeout_sec=.1)
            metrics=pose_metrics(rows,seconds)
            write_json(dest/(prefix+'_'+label+'_poses.json'),rows)
            write_json(dest/(prefix+'_'+label+'.json'),metrics)
            if label!='warmup': results.append(metrics)
    finally:
        node.destroy_node();rclpy.shutdown()
    return results


def service(action):
    subprocess.run(['systemctl','--user',action,SERVICE],check=True,timeout=20)


def pause_controller():
    from web_console import rpc
    try:
        state = rpc('pause')
    except (ConnectionRefusedError, FileNotFoundError):
        # A stopped controller can leave its Unix socket behind. Do not infer
        # process liveness from the presence of that file.
        probe = subprocess.run(['pgrep', '-f', '[p]ython.*switch_control.py'],
                               capture_output=True, text=True, timeout=5)
        if probe.returncode != 1:
            raise RuntimeError('遥操进程仍存在或无法确认退出，暂停接口不可用；未开始重定位')
        return '控制器已退出，无需发送暂停命令'
    if state.get('error'):
        raise RuntimeError('控制器故障未解除：' + state['error'])
    if state.get('phase') != 'paused':
        raise RuntimeError('无法确认已暂停跟随')
    return '已暂停跟随'


def main():
    lock=(ROOT/'.relocalize.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    dest=ROOT/'acceptance'/time.strftime('relocalize_%Y%m%d_%H%M%S');dest.mkdir(parents=True)
    result={'running':True,'phase':'准备','log_dir':str(dest),'started_at':time.time()}
    def progress(message):
        result['phase']=message;write_json(STATUS,result);print(message,flush=True)
    config=Path.home()/'.config/libsurvive/config.json'
    backup=dest/'config.before.json'
    stopped=False
    try:
        progress('暂停跟随并检查设备')
        progress(pause_controller())
        from core import load_profile
        from collect import verify_sense_hubs
        verify_sense_hubs(load_profile(ROOT/'profile_single_left.json'))
        executable=Path.home()/'pika_ros/install/pika_locator/lib/survive-cli'
        if not executable.is_file():raise RuntimeError('缺少 10 月 16 日使用的定位程序')
        shutil.copy2(config,backup)
        progress('备份完成，正在重新标定两个基站（约 30 秒）')
        service('stop');stopped=True
        config.rename(dest/'config.original.json')
        with (dest/'calibration.log').open('w') as log:
            child=subprocess.Popen([str(executable),'--force-calibrate','--lighthousecount','2'],
                cwd=executable.parent,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGINT)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL);child.wait()
        result['calibration_exit_code']=child.returncode
        if not config.exists() or not config_valid(config.read_text()):
            raise RuntimeError('未生成两个基站的完整标定，恢复旧配置')
        shutil.copy2(config,dest/'config.after.json')
        progress('重新启动定位，等待收敛并进行两轮静止检查（约 30 秒）')
        service('start');stopped=False
        checks=verify(dest,'first')
        if not all(c['output_ok'] for c in checks):
            progress('定位输出未恢复，重启一次后复测（约 30 秒）')
            service('restart'); checks=verify(dest,'retry')
        result['checks']=checks
        result['output_ok']=all(c['output_ok'] for c in checks)
        result['static_ok']=all(c['static_ok'] for c in checks)
        shutil.copy2(config,dest/'config.final.json')
        progress('短时静止检查通过，仍保持暂停跟随' if result['static_ok'] else
                 '定位仍有漂移或缺帧，请检查结果；未恢复跟随')
    except Exception as exc:
        result['error']=str(exc)
        if stopped and backup.exists():
            if config.exists():shutil.copy2(config,dest/'config.failed.json')
            shutil.copy2(backup,config)
            try:service('start')
            except Exception as restart_error:result['restart_error']=str(restart_error)
        progress('重定位失败：'+str(exc))
    finally:
        result.update(running=False,finished_at=time.time())
        write_json(dest/'result.json',result);write_json(STATUS,result)
    return 0 if result.get('static_ok') else 2


if __name__=='__main__':raise SystemExit(main())
