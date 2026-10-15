# 175：双 Pika Sense 遥操作双 Piper 的采集流程

配置经用户确认：双 Pika Sense、双 Piper 原装夹爪，保留现有三路 Orbbec。
新流程位于 `~/piper_runtime/collection`，旧 `~/pika_ros` 不修改、不重新编译。
2026-10-15 状态：录制链路软件验证通过；Pika 未接入，真实遥操作和示教采集尚未验收。

## 依据与版本差异

已阅读厂家手册及关联章节：

- [Pika ROS2 总手册](https://agilexsupport.yuque.com/staff-hso6mo/peoot3/vm7e26ho2hmuw0ng)
- [定位及校准](https://agilexsupport.yuque.com/staff-hso6mo/peoot3/hshvoqzlqlz6nbzp)
- [遥操作](https://agilexsupport.yuque.com/staff-hso6mo/gp5vq8/ey6ocmxoldt9hgn1)
- [遥操作采集](https://agilexsupport.yuque.com/staff-hso6mo/gp5vq8/ubefu683z7ap3drt)
- [数据处理](https://agilexsupport.yuque.com/staff-hso6mo/gp5vq8/dzsirqgsap6x8acp)

沿用“校准 → 遥操作 → 分 episode 录制 → 时间对齐 → 数据导出”的流程。
新版文档使用 pika_remote_agx_arm；175 安装的是 pika_remote_piper。
新入口复用旧安装版的定位器、Sense 串口桥、FK/IK、Piper 驱动，
以 legacy_manifest.json 固定其源码、URDF 和两个原生节点的哈希。
这不是完整可迁移环境包：旧 install 中的库、ROS、Conda 环境仍是依赖。

采集部分使用新的只读订阅器：三相机从现有 HTTP 服务读取，不重复打开 V4L2；
不启用旧 RealSense/鱼眼节点，不要求 Pika Gripper 的话题。
旧源码和 install 的话题配置不一致，因此不使用旧 multi_pika_teleop YAML。
录制开始/结束使用独立终端，暂不绑定 Sense 双击录制服务。
厂家新版“双击右手开遥操、左手录制”不能直接套用：旧节点是左右各自的
`/teleop_trigger_l`、`/teleop_trigger_r`，须分别验证。每次触发前对齐 Sense 与末端姿态。

## 一次性准备

1. 接入两只 Sense（包含 Tracker），确认基站供电、位置与标定；
   当前配置保留旧 Tracker ID：左 LHR-5E557D95、右 LHR-F7A7E2FD。
2. 检查 `profile.json` 的左右串口；当前固定别名为 `/dev/pika-sense-left`、`/dev/pika-sense-right`。
   如设备换口，按厂家绑定流程重新核对，不猜测 `/dev/ttyUSB0/1` 的左右。
3. 旧校准文件存在于 `~/.config/libsurvive/config.json`，不代表现场布局仍然有效。
   移动基站后按厂家说明重新校准。旧工具路径：

   ```bash
   cd ~/pika_ros/install/libsurvive/bin
   ./survive-cli --force-calibrate
   ```

   此命令需现场设备，不由检查脚本自动执行；先自行备份已有校准文件。
4. 当前执行环境是 Conda `pika`，由 run.sh 自动加载 ROS2 Humble 和旧 install。
   原环境的 cv2 位于用户 site-packages，入口沿用该依赖，不修改 piper_new。
5. 确认推理客户端已结束。激活 CAN 网络和相机服务（不等于使能电机）：

   ```bash
   sudo bash ~/piper_runtime/can_up.sh
   systemctl --user start piper-cameras.service
   ```

   新入口按已确认 USB 位置解析左右 CAN，不重命名接口：左 `1-2.2:1.0`，右 `1-2.1:1.0`。

## 日常使用

所有命令在 175 执行。先进入：

```bash
cd ~/piper_runtime/collection
bash run.sh check
```

检查依赖、Sense 串口权限、CAN 1 Mbps、旧代码哈希、校准文件及三相机新鲜度。
失败返回 2；不改权限、不启 CAN、不使能。文件存在不代表现场校准成功。

终端 A，启动定位器和两只 Sense 串口桥：

```bash
cd ~/piper_runtime/collection
bash run.sh sensors --execute
```

此入口只启动 Pika 定位及串口节点，不打开额外相机、不启动 Piper 驱动。

终端 B，在现场确认机械臂运动范围、夹爪内物体后，启动遥操作：

```bash
cd ~/piper_runtime/collection
bash run.sh teleop --execute
```

这个命令会使能机械臂。复用的旧驱动使能时会发送夹爪闭合命令，
关节运动采用旧版默认 30% 速度；**不是现推理客户端的 6% 或回零脚本的 1%**。
当前流程不自动回零；FK/IK/遥操节点启动后，以 Sense 触发来建立相对初始位姿。
首次只能先验证小幅单臂运动、左右映射、夹爪和停止行为，再进行任务示教。
旧遥操作行为尚未在当前固件/安装条件下复测，软件验收不能替代此步骤。

不带 `--execute` 仅打印计划。新入口遇到子进程退出会关闭本组，不自动重启。
启动前拒绝已存在的同类节点，以及正在运行的推理、回零或恢复进程。
此检查不能阻止另一个终端随后另行启动旧程序；现场仍须保证唯一控制源。

终端 C，遥操作触发之后，先验证全部必要数据流：

```bash
cd ~/piper_runtime/collection
bash run.sh streams
```

要有左右目标、左右反馈、左右 Pika 位姿，各恰好一个发布者，且消息新鲜。
目标尚未发布时会检查失败，这是预期行为。

通过后录制一条示教：

```bash
bash run.sh record --task '将桌面上的面包放入盘子'
```

也可限制时长：

```bash
bash run.sh record --task '将桌面上的面包放入盘子' --seconds 30
```

每次创建新的 `~/datasets/pika_piper/episode_日期_时间_PID/`，不覆盖旧数据。
Ctrl+C 或达到时长结束录制，关闭文件后自动做质量检查。
**停止录制不会停止遥操作，不会 disable/reset/电子停止，也不保证机械臂锁位。**
录制器故障仅中止录制、将本条标记 failed；操作者需要另外停止现场遥操作。
不要用 dataset replay 在真实控制域重播动作话题。

终端 B 结束遥操作后再结束终端 A。控制器可能继续完成已接收目标；
不要把进程退出当成即时物理急停或保持当前姿态。
回零使用原有 `~/piper_runtime/recover_and_zero.sh --execute`，不能与遥操作同时运行。

## 数据及质量

```text
每个 episode/
  manifest.json     任务、设备映射、依赖/采集代码哈希、时间及完成状态
  messages.jsonl    原始 ROS 消息、源时间、接收时间及单调时钟
  frames.jsonl      三相机独立时间戳和文件索引
  frames/*.npz      high/left/right 三路 640×480 BGR 无损图像
  aligned.jsonl     通过对齐的记录，保留各原始消息
  quality.json      帧数、实际帧率、间隔、拒绝原因、有效标记
  data.hdf5         手动导出的对齐数据
```

相机顺序固定：全景 high=CC1C8100041、左腕 left=CC1C810002Y、右腕 right=CC1C8100015。
读取相机服务现有方向，不额外旋转；采集前从网页核对安装方向与推理一致。

动作来自 `/joint_states_gripper_l/r`（真正送给旧 Piper 驱动的 ROS 请求目标），
状态来自 `/joint_states_single_l/r`（旧驱动反馈），禁止用状态代替动作。
向量顺序 `[左6关节, 右6关节, 左夹爪, 右夹爪]`，关节弧度、夹爪米。
动作保留请求值，旧驱动仍会进行角度换算、夹爪限幅，不将其声称为总线确认的执行值。
不使用 `/master/joint_left/right`：旧版该话题的夹爪单位与反馈不同。
旧驱动反馈时间戳是 ROS 发布时间，不是 CAN 原始采样时间，不能凭此排除底层总线数据停滞。

初始图像采样设为 10 Hz，可在自定义配置中调整；ROS 原始消息按到达逐条保存。
无损 NPZ 约 2.8 MB/帧，即约 1.7 GB/分钟；默认磁盘可用空间低于 10 GB 拒绝开始/继续。
该数据采样率不等于机械臂控制频率，也不等于推理动作频率。

三相机时间跨度不超过 100 ms，源/接收数据年龄不超过 200 ms；
对齐取三相机中最新时间作为基准，每个必要消息选此前 100 ms 内最新一条，
不从未来补动作，不把旧数据无限复用。记录重复相机帧、缺流、异常时间戳会中止。
至少 10 帧、每帧均能对齐、实际频率不低于目标 80%、最大间隔不超过三个周期才通过。
这些是初始验收阈值；快速操作可能需要更严格同步和更高图像采样率，需实测后调整。

导出：

```bash
bash run.sh validate ~/datasets/pika_piper/episode_实际编号
bash run.sh export ~/datasets/pika_piper/episode_实际编号
```

HDF5 包含 `observations/qpos`、`action`（均 N×14）、时间戳和 NPZ 相对路径索引。
原始图像目录必须随文件一起保留；拒绝导出 failed/不合格数据，不覆盖已有 HDF5。
这是采集归档格式，不声称已完成 Zero-WAM 的 28D 动作、latent 或训练集转换。
任务成功/失败还需人工检查视频内容；quality.valid 只表示数据完整性合格。

## 验证记录

2026-10-15：本地 14 项离线测试通过。175 的 ROS 接收、真实 HTTP 三相机读取、
时间对齐及 HDF5 导出已在独立 localhost ROS 域、随机测试话题下测试：
3 秒 30 帧全部对齐，实测约 9.96 Hz，最大间隔约 112 ms。
**关节/位姿是模拟消息，不是一次真实机器人示教。** 测试没有建立机器人 SDK 连接或启动控制节点。
结果保存在 `collection/acceptance/synthetic_*/`，不纳入 Git，也不进入正式数据目录。

175 当前依赖和三相机检查通过；Sense 串口未连接、CAN DOWN，遥操作验收待现场接入。
文件 legacy_manifest.json 是检查基线，不要通过随意更新哈希跳过旧程序变更审查。


## 2026-10-15 现场接入与权限修复

两只 Tracker 已接入并通过 USB 序列号核对：左 LHR-5E557D95、右 LHR-F7A7E2FD。
同一 Sense USB Hub 下的串口分别在 1-1.2.4:1.0（左）和 1-1.1.4:1.0（右）。
已部署 99-pika175-access.rules，仅为这两个串口和指定 Tracker 授权 piperpika 用户，
使用 0660 权限，没有执行全设备 chmod 777。
配置使用固定别名，检查器同时核对对应 Tracker 与串口处于同一 Hub；更换 USB 口需重新核对。

新机/重装时部署：

```bash
sudo install -m 0644 ~/piper_runtime/collection/99-pika175-access.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
sudo udevadm trigger --subsystem-match=hidraw
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=28de
sudo udevadm settle
```

相机服务此前随最后一个 SSH 会话结束而停止。已执行
`sudo loginctl enable-linger piperpika`，使用户服务在退出登录后继续运行，三相机恢复在线。

定位单独检查（约20秒，结束后自动关闭本次定位进程，不启动串口控制桥或机械臂节点）：

```bash
cd ~/piper_runtime/collection
bash run.sh locator-check
```

结果与原生日志保存到 acceptance/locator_日期_时间/。USB 已识别与定位有效是两项不同检查。
这一步不启动 CAN，不使能、不回零、不执行机械臂遥操作。


定位检查尚未通过：15:47、15:49、15:51 的三次对照中，两只 Tracker 被旧程序打开后，
均报告 `T21 Device disconnect: 1`，随后 libusb 的 pthread_mutex_lock 断言失败。
左右位姿消息计数均为 0。去掉 Conda LD_PRELOAD、临时改用另一份已安装 libsurvive 库，
都没有解决。没有替换旧库或强制重标定；内核日志未见同期 USB 拔出/重新枚举，
因此不能直接认定为物理掉线，也不能认定只是基站未开启。
所有探测进程已结束，CAN 仍为 DOWN，未启动机械臂控制。
校准文件存在性不代表有效，libsurvive 初始化时会更新其配置文件时间。
还需结合现场 Tracker 电源/基站状态进一步检查定位链路。

### 基站确认后的底层诊断

现场确认两个基站均绿灯常亮且无遮挡。随后用 GDB 和 LIBUSB_DEBUG=4 对旧定位节点
作短时诊断，未启动任何机械臂节点。日志在 acceptance/usb_diagnosis/：

- gdb.txt：确认 SIGABRT 出现在 libusb 的互斥锁断言，旧定位器进程终止。
- libusb_debug.txt：首先出现 `urb type=1 status=-71 transferred=0`、
  `low-level bus error -71`；随后 T21 被库标为 Device disconnect，
  清理传输时触发互斥锁断言。USB 传输错误在先，进程崩溃在后。

这将当前阻塞定位到 Tracker USB 数据链路及旧驱动错误处理，但尚不足以区分
线缆、Hub/供电、Tracker 工作状态或软件兼容性。不能仅凭基站正常就认定 Tracker
已开机正常工作，也不能仅凭 -71 就断言某根线已损坏。
尚未有任何有效左右位姿，不解除采集/遥操作前置检查，不绕过错误继续运行。

### 两只 Tracker 绿灯后的复测（2026-10-15 16:20）

现场确认两只 Tracker 均已按键开机、亮绿灯。再次运行 `bash run.sh locator-check`，
左右位姿仍均为 0，进程以 -6 提前退出；日志在 `acceptance/locator_20261015_162011/`。
另用已安装的 `survive-cli` 绕过 ROS 节点，使用校准配置副本、禁用校准，限时 12 秒测试，
同样约 0.92 秒报 `T21 Device disconnect: 1` 和 libusb 互斥锁断言。
日志及配置副本在 `acceptance/powered_on_cli_20261015/`。
因此故障可在底层工具中复现，尚不能归因于 ROS 话题配置或仅仅未开机。
下一步应分别测试单只 Tracker，并对比绕过 Sense Hub 的 USB 直连，区分设备、连接链路及驱动问题。
测试未启动机械臂控制；单只测试和直连测试尚未进行。

### 环境与单只 Tracker 对照（2026-10-15 16:22–16:24）

对照厂家主文档第四章部署要求，实测如下：

| 项目 | 175 实测 | 判断 |
| --- | --- | --- |
| 系统 / ROS | Ubuntu 22.04.5 / Humble，内核 6.8.0-138 | 系统与 ROS 符合文档要求 |
| 代码 | pika_ros ros2 分支，c30feb2b5e04516326609e82602eadf682a8755f | 旧版部署，未直接升级共享工作区 |
| 编译及定位相关依赖 | gcc/g++ 13、zlib、libusb 开发包、JSONCPP、pcap、BLAS/LAPACK、OpenVR、diagnostic-updater 已装 | 未发现这些依赖缺失 |
| 定位节点动态库 | source ROS 和 pika_ros 后，ldd 无 not found | 未 source 时的缺库不能视为实际启动故障 |
| 原生二进制 | 定位节点及两份 libsurvive 的 SHA256 均匹配本机 source/install.zip | 未发现部署后被改写；不证明旧驱动无缺陷 |
| USB 权限与电源 | 已有厂家规则及按序列号的补充规则；右手 power/control=on、runtime=active | 当前证据不支持权限拒绝或自动挂起 |
| Python 依赖 | pika 环境 NumPy 2.0.2 / OpenCV 4.13.0 | NumPy 不符合当前文档 numpy<2；独立原生定位器不使用它，不能据此解释崩溃 |

单只对照保持同一主机、同一定位程序及各自原有 Sense/短线：

- 左手 LHR-5E557D95：20 秒探测未提前退出，128 帧通过有限值、四元数范数和时间戳检查。
  日志 `acceptance/locator_20261015_162229/`。仍有大量定位波动警告；这不等于精度、稳定性或遥操作验收通过。
  双臂检查器仍返回 false，因为右手被物理拔除。
- 右手 LHR-F7A7E2FD：约 0.89 秒 USB Device disconnect 后 libusb 断言退出，0 帧。
  日志 `acceptance/locator_20261015_162345/`。右手单只时临时名称变成 T20，不能用 T20/T21 固定代表左右。
- 右手在 `env -i` 清空环境、仅设置 HOME/PATH/定位库路径后运行独立 survive-cli，
  仍约 0.89 秒复现。使用配置副本并禁用校准，日志 `acceptance/right_clean_env_20261015/`。

结论：当前基本环境能运行左手定位；右手问题不依赖双设备同时连接，也不依赖 ROS/Python/Conda 环境。
尚不能区分右手 Tracker、本侧短线/Hub/供电，以及旧驱动对该设备的兼容性。
下一项交叉验证应将右手 Tracker 接到左手已能输出数据的短线和 Sense 通道，或使用已知正常数据线直连工控机。
未全局升级依赖、未覆盖旧定位库、未启动机械臂。左手输出波动需要在 USB 故障解决后另行验证校准和稳定性。

### 更换主机 USB 通道后的测试（2026-10-15 16:28）

确认右手 Tracker LHR-F7A7E2FD 出现在原左侧 `1-1.2.3.1`，当前仅一只 Sense 串口在线。
定位探测仍 0 帧、提前退出 -6，日志 `acceptance/locator_20261015_162858/`。
故障在更换主机 USB 通道后仍复现；尚未确认是否同时更换了 Sense 和 Tracker 短线，
因此不能将本次测试描述为完整已知正常链路上的 Tracker 交叉测试，也不能断定 Tracker 损坏。
