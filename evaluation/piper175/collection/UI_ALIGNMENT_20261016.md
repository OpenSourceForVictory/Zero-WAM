# 2026-10-16 采集界面和参数对齐

当前地址：Pika 192.168.137.54:8081；参考 Aloha 192.168.137.160。
参考来源：/home/agilex/cobot_magic/collect_data/templates/index.html、web_server.py、collect_interactive.py 和 static 中的 Vue/Tailwind 文件。

已移植浅色布局、数据管理/采集分页、交互采集会话、Enter 开始/保存、D 丢弃/删除最近一集、最大帧数自动保存。保留单 Sense 切换左/右臂、暂停跟随，以及 Q 退出会话。退出会话暂停跟随并丢弃尚未保存的条目；删除移入回收站。双臂和三路图像始终共同记录。

读取参考机器 web_config.json 得到 frame_rate=30、max_timesteps=5000。Pika 持久配置 ~/datasets/pika_piper/.console_config.json 已设 fps=30、max_frames=5000；网页/服务端默认值、双臂采集 profile、转换默认帧率同步更新。30 Hz 对应每条约 166.7 秒。旧数据仍保持原来的 10 Hz，转换旧数据需选择 10 Hz，不得混合帧率转换或改写旧数据帧率。

原始 HDF5 保留 14 维实际下发目标，转换按既定 legacy28 从实测关节/末端位姿构造 28 维动作，输出 LeRobot v3。未移植参考机器的升降控制和推理页。

部署目录：~/piper_runtime/collection；服务：systemctl --user restart piper-collection-web.service。重启前确认无控制或采集会话。
本次参数修改前备份：~/piper_runtime/backups/capture_config_20261016_152545。
先前已保存版本：collection_20261015_213731_1f22008。首次界面对齐时额外备份命令因远端没有 python 命令而失败，原先保存的版本仍在；后续命令使用 pika_export 环境的绝对 Python 路径。

验证：43 项本地测试通过，含连续两条自动保存、会话锁、路径校验；浏览器模拟接口 14 项快捷键测试通过。实际网页三路实时画面可用；既有 128 帧、10 Hz 轨迹的元信息和三路图片读取成功。部署后 API 确认 30 Hz / 5000 帧。尚未开展 30 Hz 真机连续录制的吞吐与同步验收，本次未驱动机械臂。

后续启动故障：重启后 CAN 为 DOWN，已恢复至 1 Mbps；用户授权后使用 recover_and_zero.sh --execute 完成双臂使能归零。之后控制服务启动正常，但切换左臂报 Sense/Tracker input missing or stale，查明传感器 ROS 节点未启动。启动 pika-collection-sensors.service 后 dual profile streams 检查全部 fresh。新增用户服务单元（仅传感器，无机械臂控制；不自动重启）并接入网页启动控制入口。单元安装至 ~/.config/systemd/user/ 后 daemon-reload。网页源码已部署，为保留当前控制会话未立即重启，自动启动逻辑在下次网页服务重启后生效。当前传感器服务已运行。

30 Hz 故障修复：旧录制器把暂时重复的相机帧直接当作致命错误；失败状态的 Enter 按钮同时被禁用。现改为在 max_age 内等待三路新帧，停止请求可打断等待，冻结/时间倒退仍报错；相机 HTTP 接口使用固定 epoch_offset，避免同一帧在每次请求时产生换算抖动。失败条目支持 Enter 结束并保留原始文件，页面显示失败原因。已部署 collect.py、web_console.html、camera_web.py 并重启相机服务，未重启或移动机械臂控制。
验证：45 项测试通过；浏览器 15 项模拟测试通过（新增失败条目 Enter 结束）。真机静止 5 秒录制 97 帧，97 帧对齐、无重复帧报错，但 achieved_camera_hz=19.2009，未通过 30 Hz 质量门槛。诊断数据位于 collection/acceptance/frame_wait_validation/episode_20261016_155423_102970，未纳入训练集。30 Hz 吞吐仍待优化，不能声明已达标。参考174使用 ROS 各路 deque 按时间戳消费帧；当前175为最新帧 HTTP 快照，取帧结构尚不一致。

## 30 Hz 验收完成（2026-10-16 16:00）

相机增加每路 12 帧有界队列；/observation.npz?after=[...] 根据三路上一帧时间戳返回各路下一帧，独立读者不互相消费队列。无 after 参数仍返回最新帧，保持推理兼容。服务端按 200ms 超时/帧龄拒绝冻结或积压，录制端保留时间差和质量检查。/health 增加 capture_hz 和最近帧间隔，方便分辨采集源与录制链路瓶颈。

关键瓶颈定位：三路 V4L2 只有 1 个缓冲区时，源采集率约19.2 Hz，帧间隔交替约36/64ms；改为4个缓冲区后，三路源输出恢复约30 Hz。曾测试关闭 exposure_auto_priority，效果不变，已全部恢复原值1，未把该试验设置固化。当前仍保留原自动曝光、分辨率、MJPEG和安装方向。

15 秒实采：450帧，450对齐，0拒绝，29.9314 Hz，最大相机间隔39.615ms，valid=true。
原始诊断目录：~/piper_runtime/collection/acceptance/queue_validation/episode_20261016_155948_107846。
网页真实 API 验证 session_start→start→save→exit 全部成功；保存至 ~/datasets/pika_piper/validation_30hz_20261016/hdf5/episode_0.hdf5（静止系统验证，勿作为任务训练示教）。此测试结束后无采集会话，双臂保持暂停跟随；持久采集配置仍为 pika_dual_demo / 30 Hz / 5000帧。
本地46项测试通过，包括帧队列不重复消费、保留中间帧、多读者互不影响；前一轮浏览器15项快捷键测试包含失败条目Enter结束。
旧10 Hz数据不要与新30 Hz数据混在同一批转换中；新任务采集宜另建任务名称。

## LeRobot v2.1 与参考机对齐

根据用户要求，转换默认改为 v2.1。2026-10-16 从参考机 /home/agilex/cobot_magic/collect_data 重新读取 convert_to_lerobot_v21.py 和 action_layouts.py；保存为 reference_174/convert_v21_20261016.py / action_layouts.py，仅把 FK 导入改为已有固定版离线 piper_fk。沿用参考机的 legacy28、14维 observation.state、每episode parquet/三路MP4和meta JSONL结构。包装器保留输入FPS/RGB核对、原始HDF5不改写、来源映射和控制切换标签。--version v3.0 可显式选择旧格式，默认v2.1。

独立静止测试163帧转换并校验通过：codebase_version=v2.1、action=[28]、state逐值与HDF5一致、三路视频各163帧、163条控制模式标签，timestamp与next.done检查通过。输出 ~/piper_runtime/collection/acceptance/lerobot_v21_validation_20261016。
转换脚本/网页已部署，默认v2.1立即生效；web_console.py中新的目录命名和目录列表版本显示需下次网页服务重启，未打断现场采集会话。

现场另见控制器 fault: Invalid feedback（16:08:11），当时日志未保存原始读数。随后只读CAN连续5次显示右夹爪原始角度-70（即-0.07mm）、左夹爪46.1mm，两臂NORMAL/Error Code=0。现有read_arm要求夹爪>=0，该值足以触发全局故障；不能据此声称历史触发瞬间也是相同数值。未修改阈值、未恢复跟随或重启控制器。

按用户要求核对参考机 ROS 驱动：piper_start_slave_node.py:187 直接发布 grippers_angle/1000000，piper_start_ms_node.py:230 同样直接发布；0..80000 限制在下发目标回调中，没有反馈范围停机检查。因此取消 switch_control.read_arm 的反馈0..0.08m范围检查，保留有限值/硬件错误/通信新鲜度/使能检查；命令限幅与参考机保持一致。49项测试通过。部署后退出旧故障控制进程（已清理前端，无机械臂目标下发）；进程曾短暂成为待父进程回收的僵尸，网页服务重启后完成清理。未自动恢复跟随。只读真机验证连续5次接受右夹爪-0.07mm，双臂使能/状态检查通过。

## 配对漂移修复（16:21）

用户正式采集出现 Camera skew exceeded：原按每路下一帧逐个消费，在各路丢帧不均时会累积配对偏移。已改为参考机共同时间规则：先排除各读者已使用的帧，再取各路最新帧时间戳的最小值作为 frame_time，各路选不早于 frame_time 的首帧；过早帧不再参与当前配对。保持100ms配对门槛和200ms新鲜度/等待超时，不放宽检查。无游标的推理最新帧接口保持不变。
本地50项测试通过，新增单路丢帧后重新配对和缺失帧等待验证。真实30秒静止采集898帧，全部对齐、0拒绝、29.91597Hz、valid=true。原始诊断目录 acceptance/common_time_validation/episode_20261016_162107_133420。该软件配对使用主机收帧时间，不能视为硬件同步曝光。

转换页结果展示：新增按所选任务显示的结果卡片（版本/动作维数/轨迹/帧数/FPS/输出目录及列表入口）；转换作业状态变化后自动刷新目录，显示进行中/失败。成功校验标记只在后台完成日志与目录匹配时显示，历史无日志结果仅标为已生成。仅更新HTML，无需重启服务。浏览器验证实际pika_aloha_10_16结果显示v2.1、28维、3条/878帧/30Hz及完整路径。


## 故障后恢复（17:08）

现场控制器曾锁存 Stale CAN joints feedback（约0.332秒）。退出会话调用 pause 虽已成功，网页仍因返回的历史 error 抛错，导致无法完成退出。修复 control_command：pause/stop 成功返回时允许带有历史故障；RPC命令本身失败仍报错。16项网页测试通过，包含历史故障不阻止成功停止的回归测试。

本次先 switch.sh stop 退出旧控制器，再重启 piper-collection-web.service 和 pika-collection-sensors.service。只读检查双臂连续5组反馈及使能正常。通过网页 controller_start 重新建立控制器，状态 connected=true、execute=true、phase=paused、error=null，双臂保持当前位置，未自动选择控制臂或归零。日志 acceptance/dual_switch_20261016_170819。

定位尚未恢复：streams 检查报 pika_left: missing/invalid message，官方定位节点持续输出 The data fluctuates too much。已要求现场将左手Tracker放回无遮挡位置并静止，再复核。控制服务恢复不等于遥操数据已就绪。独立 locator-check 因已有定位进程拒绝运行，空的 locator_20261016_170427 目录不代表测试通过。
