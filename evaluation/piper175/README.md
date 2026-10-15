# 175 Piper 双臂运行环境

部署位置：`piperpika@192.168.8.175:~/piper_runtime`。
使用已有 `piper_new` Python 3.10 环境；`run.sh` 清除 PYTHONPATH、LD_PRELOAD，
关闭用户 site-packages，避免加载 pika / 用户目录中的同名包。
不修改原 Lingbot、Pika、ROS 安装，不依赖推理服务。

## 启动和查看

### 网页实时查看

同一局域网浏览器打开 `http://192.168.8.175:8080/`。
网页从左到右为左腕、顶部全景、右腕；支持全屏、暂停、断线状态与自动重连。
三路相机各采集一次并共享 JPEG，网页刷新最高约 10 FPS。
该页面仅提供相机查看，不提供机械臂控制。地址仅在局域网或可达该网段的 VPN 中可用，
未配置公网转发或登录认证。

服务以当前用户 systemd 管理，用户登录后自动启动，异常退出自动重启：

```bash
systemctl --user status piper-cameras.service
systemctl --user restart piper-cameras.service
systemctl --user stop piper-cameras.service
```

网页服务会占用相机。运行下面的桌面预览/截图前先停止服务，完成后执行
`systemctl --user start piper-cameras.service` 恢复网页。
服务配置位于 `~/.config/systemd/user/piper-cameras.service`，
网页代码位于 `~/piper_runtime/camera_web.py` 和 `camera_web.html`。

### 桌面窗口和截图

在 175 的终端执行：

```bash
cd ~/piper_runtime
bash can_up.sh
bash run.sh arm.py status --arm left
bash run.sh arm.py status --arm right
bash run.sh cameras.py
```

相机窗口同时显示三路 RGB，Q 或 Esc 关闭。必须在有桌面 DISPLAY 的会话中运行。
无桌面时可以保存三张图片（输出目录须尚不存在）：

```bash
bash run.sh cameras.py --snapshot /tmp/piper-capture-001
```

预览与截图会占用同一批相机，请先关闭预览再运行截图或其他相机程序。
2026-10-12 用户确认相机角色：

| 角色 | 序列号 | 原网页标识 |
|---|---|---|
| 左腕 / cam_left_wrist | CC1C810002Y | B |
| 顶部全景 / cam_high | CC1C8100041 | 顶部 |
| 右腕 / cam_right_wrist | CC1C8100015 | A |

网页按左腕、顶部、右腕排列。`cameras.py` 的 `ROTATION_DEGREES` 按序列号配置方向：
2026-10-13 按现场图像复核，取消腕部 180° 补偿，三路均为 0°。网页 JPEG、截图和桌面预览
都使用修正后的图像；网页不再额外旋转。旧桌面窗口仍按序列号顺序显示。
这是此运行目录的采集层设置，不改变相机固件或其他 ROS/Lingbot 采集程序。
后续推理应复用此 Camera 类，或采用相同配置且仅旋转一次。

## 控制

机械臂按 USB 物理口选择，避免重启后 can0/1 枚举顺序变化：

| 标识 | USB 口 | 本次接口 | 固件 |
|---|---|---|---|
| left（左臂）/ usb22 | 1-2.2:1.0 | can0 | S-V1.7-3 |
| right（右臂）/ usb21 | 1-2.1:1.0 | can1 | S-V1.7-3 |

两臂 SDK profile 使用与实测固件对应的 `default`。
2026-10-12 用户确认正在示教的 usb21 为右臂，另一只 usb22 为左臂。
`left/right` 与 `usb22/usb21` 均可使用，按 USB 物理口解析，不依赖 can0/1 枚举顺序。
`can_up.sh` 仅配置 1 Mbps 通信，不使能、不回零；不安装开机自启。

状态查询不使能、不改变控制模式。其他命令默认仅预览，加 `--execute` 才发送。
现场确认臂周围空间、退出示教模式后，可分别使能和小角度点动：

```bash
bash run.sh arm.py enable --arm usb22 --execute
bash run.sh arm.py jog --arm usb22 --joint 1 --delta-deg 1
bash run.sh arm.py jog --arm usb22 --joint 1 --delta-deg 1 --execute
```

点动只支持单臂单关节，最多 ±2°、速度 1%，保持其他关节目标与当前反馈一致，
不发送夹爪指令。执行前要求新鲜、无故障、非示教状态和六个电机已使能；
执行后检查到位反馈。它不提供碰撞检测，仍需现场观察。
退出时只断开 SDK，不回零、不复位、不失能。
运动异常/超时发送电子停止；电子停止可能导致阻尼下落，不能视为位置保持。
手动电子停止入口为 `bash run.sh arm.py stop --arm usb22 --execute`。

## 2026-10-12 验证记录

- 两路 CAN 激活到 1 Mbps，关节/状态反馈约 200 Hz，未报告关节通信故障。
- 两臂固件均为 S-V1.7-3；usb22 待机且六个电机未使能。
- usb21 处于示教模式，现场检查期间从 START_RECORDING 变为 STOP_RECORDING，
  关节位置也有变化。未自动退出示教或下发运动。
- 三路相机顺序读取及同时读取均成功，输出为 640×480 BGR；Tk 预览无需 OpenCV GUI。
- piper_new 缺少 Tk 脚本目录，已从本机 pika 中相同 Conda 包
  `tk-8.6.15-h54e0aa7_0` 补齐 `lib/tk8.6`，未改变 Tk 版本。
- 状态查询、预览以及反馈保护单元测试完成；使能和实际运动尚未验证。
- 174 是 ROS 1 Noetic + ROS 相机；175 保留 ROS 2 Humble，用 V4L2 直接采集。
  未复制 174 的左右映射、TCP、夹爪缩放或推理服务配置。

2026-10-13 回零实测：按用户授权和现场退开确认，以 1% 速度先左后右 MOVE_J 回零，两臂最终六关节反馈均为 0°，状态 NORMAL，保持使能。未修改零点标定，未发送夹爪命令。左臂首次单次目标未启动运动，确认 MOVE_J 后重发同一目标成功；go_zero.py 已加入模式等待与短时重复目标。日志：~/piper_runtime/zero_checks/20261013_left_01 和 20261013_right_01。编码器到零不等于机械零位标定已验证。
