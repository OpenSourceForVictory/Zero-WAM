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

### 右手恢复输出（2026-10-15 16:33）

现场再次调整连接后，右手 LHR-F7A7E2FD 出现在 `1-1.2.3.3`。
同一软件环境运行 20 秒，收到右手 1845 帧，全部通过有限值、四元数范数和时间戳基本检查，
进程无提前退出，日志中无 `fluctuates too much` 警告。
日志 `acceptance/locator_20261015_163303/`。左手未接入，双臂检查器总结果仍为 false。
这支持优先检查原 USB 连接链路，不支持立即重装软件或断言右手 Tracker 损坏。
尚未进行双只并行验收和绝对精度验证。

虽然本次现场操作是在请求直连后完成，USB 拓扑仍显示经过 VIA Hub、两级 Genesys Hub，
且与一个 Sense 串口共享 `1-1.2` 父节点；因此不将本次记录为已验证绕过全部 Sense/Hub 的直连。
当前连线应先保留，后续恢复左手时重新核对 Tracker 序列号与 Sense 串口绑定，不能直接沿用原端口映射。

### 切回连接后故障复现（2026-10-15 16:54）

用户确认将 Tracker 连接线切回后，再测右手 LHR-F7A7E2FD，USB 路径为 `1-1.1.3.2`。
约 0.898 秒出现 Device disconnect 和 libusb 互斥锁断言，退出 -6，左右均 0 帧。
日志保存在 175 的 `acceptance/locator_20261015_165412/`。
对照 16:33 相同 Tracker 在 `1-1.2.3.3` 收到 1845 帧，这进一步支持连接链路相关故障；
由于 USB 父通道也发生变化，尚不能把原因单独归到某根短线，仍需区分短线、接口和 Sense Hub。

### 连接拓扑核查（2026-10-15 16:55）

只读脚本 `python3 inspect_usb.py` 可保存 USB 设备、序列号、电源状态及串口映射，
不打开设备、不启动驱动。175 快照：`acceptance/usb_connection_20261015_1655.json`。

- 当前两套 Sense 的串口和 D405 均枚举在线；仅右手 Tracker 在线，左手 LHR-5E557D95 未枚举。
- 右侧通道：`1-1.1` 主 Hub → `1-1.1.3` 子 Hub → `.2` 右手 Tracker；
  串口为 ttyUSB0，固定别名 pika-sense-right；D405 序列号 242623072154。
- 左侧通道：`1-1.2` 主 Hub → `1-1.2.3` 子 Hub（当前无 Tracker）；
  串口为 ttyUSB1，固定别名 pika-sense-left；D405 序列号 242623072141。
- 两支路共同上游是 VIA Hub `1-1`。16:33 成功测试也经过左支路 Hub，不是已确认的绕过 Sense 直连。
- 当前 Tracker authorized=1，设备节点归 piperpika 所有，power/control=on，runtime=active；
  上游右支路 Hub 也 active。未发现定位进程或 fuser 报告的当前用户占用。
- Tracker 三个 HID 接口当前 unbound。此前 libusb 驱动会接管接口，这不能单独作为硬件故障证据。
- 内核记录 16:51:06 从左支路拔出、16:51:18 在右支路重新枚举；16:54 定位程序崩溃时无对应内核拔出记录。
  因此程序的 Device disconnect 指向传输错误处理，不等同于系统检测到物理断线。

Hub 无唯一序列号，串口别名按端口绑定；本段左右指既有端口映射，不能仅凭它确认物理手持设备左右。

### 左手接入右侧支路对照（2026-10-15 17:02）

现场更换后核实接入的是左手 Tracker LHR-5E557D95，路径 `1-1.1.3.3`。
同一定位程序仍以 -6 提前退出，左右均 0 帧；日志 `acceptance/locator_20261015_170217/`，
连接快照 `acceptance/usb_cross_left_20261015.json`。
因此失败不只发生于右手 Tracker，证据进一步指向右侧连接支路或该拓扑下的驱动问题。
注意右手上次路径是 `1-1.1.3.2`，本次末级 Hub 端口为 `.3`，并非严格相同物理端口的单变量测试。
两只 Tracker 均曾在左侧支路输出数据；尚不能判定具体线缆、Hub 或供电元件损坏。

### 右侧 Sense 更换上游 USB 连接后（2026-10-15 17:06）

用户将左手 USB 接到右手后，左手 Tracker LHR-5E557D95 从 `1-1.1.3.3` 移到
`1-1.2.3.3`，仅一套 Sense 串口在线。D405 序列号仍为 242623072154（原右侧 Sense 的相机），
表明不能把变为 `1-1.2` 的端口路径当作换用了另一只 Sense。
定位仍约 0.920 秒崩溃、退出 -6、0 帧，日志 `acceptance/locator_20261015_170622/`。
故障随原右侧 Sense 所在组合连接移动到另一上游端口，原右侧电脑 USB 端口不是故障的必要条件。
结合两只 Tracker 在该组合下均失败，应优先检查该 Sense 的 Tracker 接口/内部 Hub、短线及供电；
仍未区分这些组件或排除驱动对该 Hub 的兼容性问题，不据此断言 Sense 本体损坏。

### 单变量 Sense 对照流程

用户选择暂不进行 Tracker 直连工控机测试，改做 A → B → A。
A 为原左手 Sense（内置 D405 242623072141），B 为原右手 Sense（242623072154）。
固定右手 Tracker LHR-F7A7E2FD、同一根短线、同一根长线和电脑 USB 口，仅更换 Sense。
各轮接线由现场确认，再核对 USB 拓扑和 D405 序列号，不根据端口名称猜测 Sense 身份。

```bash
cd ~/piper_runtime/collection
python3 inspect_usb.py
bash run.sh locator-check --seconds 60 --side right
```

检查器保存运行前后 USB 拓扑、位姿计数、探测全程平均帧率、首帧等待、最大帧间间隔、
结束前静默时长和退出码。`--side right` 只要求右手输出，不会因左手未连接而直接失败。
`passed` 仅表示基本输出检查，不代表定位精度或全程稳定性验收；间隔统计另行检查。
每轮结果应结合内核日志记录。尚未完成 A/B/A；直连基准缺失，不能完全排除驱动兼容性。

### A1 结果与后续测试时长（2026-10-15 17:25）

核对左手 Sense（D405 242623072141）与右手 Tracker LHR-F7A7E2FD 后进行 A1。
日志 `acceptance/locator_20261015_172530/`：60.008 秒、5850 帧全部通过基本检查，
全程平均 97.49 Hz，首帧 1.518 秒，最大帧间隔 0.01108 秒，无提前退出，passed=true。
用户随后要求缩短测试：后续 B/A2 使用 `--seconds 5 --side right`，只用于筛查约 0.9 秒崩溃，
不能将 5 秒通过表述为长期稳定性验收。A1 完成，B/A2 尚待现场更换 Sense。

### B 对照结果（2026-10-15 17:29）

现场按要求只更换为右手 Sense；软件核实 D405 变为 242623072154，
右手 Tracker 仍为 LHR-F7A7E2FD，USB 路径仍为 `1-1.2.3.3`。
请求 5 秒测试，实际 1.106 秒即退出 -6，0 帧，passed=false。
日志 `acceptance/locator_20261015_172910/`。
A1 正常而 B 失败，进一步支持故障与右手 Sense 相关；仍需换回 A 复测形成 A/B/A 闭环，
并区分 Sense 硬件问题与旧驱动对其 Hub 的兼容性，不能仅凭此测试认定硬件损坏。

### A2 换回复测完成（2026-10-15 17:35）

再次核对左手 Sense 的 D405 242623072141 和同一右手 Tracker LHR-F7A7E2FD，
Tracker USB 路径保持 `1-1.2.3.3`。5.007 秒收到 348 帧，均通过基本检查，无提前退出，passed=true。
首帧等待 1.537 秒，最大帧间隔 0.01091 秒；全程平均 69.50 Hz 包含启动等待，不代表稳定输出阶段降至此速率。
日志 `acceptance/locator_20261015_173545/`。
现场按固定线缆及电脑端口、仅替换 Sense 的流程完成 A1 正常（5850 帧）→ B 失败（0 帧）→ A2 恢复（348 帧）。
这强烈支持故障随右手 Sense 组合出现，可向厂家提交对照证据，重点排查其接口/Hub/供电及驱动兼容性；
尚未有电气检测或固件对照，不能断言具体硬件元件损坏。未执行机械臂动作。

### 用户按右手 Sense 复位按钮后复测（2026-10-15 17:46）

用户报告已按右手 Sense 的复位按钮；尚未由厂家文档确认此按钮的功能及复位范围。
实测接入右手 Sense（D405 242623072154）和右手 Tracker LHR-F7A7E2FD，路径 `1-1.2.3.3`。
请求 5 秒，实际 1.106 秒即退出 -6，0 帧，passed=false，故障未恢复。
日志 `acceptance/locator_20261015_174633/`。此结果不证明按钮操作已完成厂家定义的复位流程。

### 单 Sense 左臂采集准备及当前阻塞（2026-10-15 18:34）

右手 Sense/Tracker 待寄修，用户选定首次控制左侧 Piper。
新增 `profile_single_left.json`，只启动左手 Sense 串口及左臂控制节点；定位器仍为旧双定位节点，
但录制和遥操作前置检查仅要求左侧输入。传感器模式不再要求 CAN 已启用；机械臂模式仍检查 CAN。
旧串口节点处理别名存在不安全字符串拼接，启动器先解析为实际 tty 路径，旧二进制未修改。

```bash
cd ~/piper_runtime/collection
bash run.sh --profile profile_single_left.json sensors --execute
# 仅列出控制计划，不执行：
bash run.sh --profile profile_single_left.json teleop
```

当前未启用 CAN、未使能或启动机械臂控制，不能据此宣称整套真实采集系统已验收。
单臂 HDF5 action/qpos 为左臂 7 维（6 关节+夹爪），不填造右臂目标。
右臂反馈若已有发布者则保留为可选原始流；目前没有验证或启动被动右臂反馈节点。
此版本尚未实现运行中切换左右臂，也不是 14 维双臂训练数据的直接替代。

已通过：三路相机实时检查；左手 Tracker 5 秒 344 帧；双臂及单臂隔离软件录制/导出各 30 帧全部对齐。
单臂合成测试目录 `acceptance/synthetic_5656cba8cf184b95bdd5407963dccab9/`，约 9.99 Hz、最大相机间隔 0.105 秒。
合成关节话题不是机器人示教。定位检查曾因循环变量覆盖将 expected_side 错写为 right，现已修复；
该次 344 帧实际来自左侧，判断使用的 selected 列表正确。

真实输入联合测试 `acceptance/single_inputs_20261015_183443/`：定位 1261 帧、Sense 1763 帧，
Sense 全部 error=true，角度被旧驱动截到 1.67 rad，距离约 0.0967 m，因此测试失败。
原始串口 `acceptance/raw_sense_20261015.bin` 显示 AS5047 rad=1.73（约 99.08 度），
旧代码允许范围为 0..1.67 rad。尚需现场捏动手柄验证动态范围，不能据此认定传感器损坏或擅自改零点。
遥操作前置检查已增加 Sense error/非有限值拒绝。所有短时测试进程均由脚本清理；无机械臂动作测试。

现场捏住手柄后只读采样 `acceptance/sense_held_20261015_183717.bin`：625 个角度为 0.02..0.03 rad，
全部处于旧驱动范围内，证明传感器响应动作。随后联合复测 `acceptance/single_inputs_20261015_183728/`
收到定位 1271 帧、Sense 1775 帧，其中 1081 帧带 error，角度覆盖 0..1.67，仍未通过。
需完成自然松开/完全捏合两端标定与映射检查，不能绕过错误直接使能机械臂，也不能将此故障归结为传感器损坏。
当前已通过 16 项离线数据检查及语法检查；实际机械臂使能、反馈、IK、夹爪动作及真实示教闭环尚未验收。
