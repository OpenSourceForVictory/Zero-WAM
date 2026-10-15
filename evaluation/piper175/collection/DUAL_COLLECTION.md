# 175：单 Sense 切换双 Piper、双臂三相机采集

当前硬件是一只正常左手 Sense + Tracker、双 Piper 原装夹爪、三路 Orbbec。
同一条 episode 内顺序操控左右臂；不能同时独立操控两臂。

## 已验证范围

2026-10-15：旧单臂遥操已实机跟随，夹爪实测约 1.6～79 mm；新双臂切换入口的 CAN 双臂读取、
左→右→左 FK/IK 预览已通过。新入口已完成左臂实际跟随、右臂保持测试（用户现场确认）：
`acceptance/live_dual/episode_20261015_205852_31480` 含 200 帧，10.002 Hz，全部对齐；
左夹爪反馈变化 67.9 mm、关节有运动；右臂六关节及夹爪反馈变化均为 0。
右臂跟随、暂停后切回左臂均已由用户现场确认。连续记录
`acceptance/live_switch/episode_20261015_210031_31954` 含 1242 帧、124.10 秒、10.0002 Hz，
1242 帧全部对齐，三路视频各 1242 帧，28 维转换及逐帧校验通过。
右臂活动段左臂反馈变化为 0；切回左臂后右臂最大关节反馈变化 0.089°、夹爪变化 0。
验收后已退出控制，无 FK/IK 残留；双臂保持使能，未归零、未下电。
合成双臂 ROS 数据 + 真实三相机录制 30 帧，10.008 Hz、全部对齐并导出；不是实际任务示教。
兼容 HDF5 已被 174 原转换脚本的 inspect_episode 读通过，含 14 维状态/目标、三路 RGB 图像、切换标签。
175 独立 pika_export 环境已完成 LeRobot v3.0 端到端转换：legacy28 动作、14 维状态、三路视频各 30 帧、30 条切换标签，10 Hz；逐帧对照通过，pip check 无依赖冲突。
验收数据位于 `acceptance/aloha_compat_lerobot28_test/`，报告为 `acceptance/lerobot28_verification.json`。
100 组双臂 FK 与 174 原环境结果完全一致，其中 3 组留作固定回归样本；固定 FK 回归测试通过。

新入口复用旧安装版 FK/IK、原夹爪几何换算；双臂 CAN 控制和切换管理是新增代码，非厂家原版双 Sense 程序。
旧驱动的 MOVE_J 30%、夹爪参数 1000、0..80 mm 范围保持一致，不增加 5 mm 行程/5 mm/s 限制。
两臂必须已经使能且处于 CAN_CTRL/NORMAL；入口不自动使能、不回零、不改零点、不下电。

## 网页控制台（推荐）

访问 `http://192.168.8.175:8081/`，或在 175 桌面双击 **Piper双臂数据采集**。
打开页面只显示画面和状态，不自动使能、归零或启动遥操。

1. 确认 Sense/相机服务已运行、双臂已使能且运动范围已清空；拿好 Sense，勾选准备完成，再点“启动控制”。
2. 将 Sense 朝向与目标臂末端一致，点“切换左臂”或“切换右臂”；观察状态变为正在跟随。
3. 填写任务名称和提示词，点“开始本条”，等待显示“正在录制”后示教。
4. 点“结束并保存”：停止本条，质量检查通过后生成 HDF5；遥操继续，随后可开始下一条。
5. “删除本条”：停止当前条且不纳入数据集，原始文件保留并标记未接受。
6. “退出录制”：先暂停跟随，再放弃尚未保存的当前条；已保存数据保留。结束操作后再点“退出控制”。
7. 已采集列表中的“移入回收站”将该条原始目录和对应 HDF5 移至任务的 `.trash/`，可手动恢复；已有 LeRobot 副本不联动删除。
8. “转换为 LeRobot · 28 维”：转换当前任务已保存 HDF5，输出新目录并执行逐帧/视频校验。转换期间不能新增或删除采集数据，暂停控制仍可用。

切换可发生在同一条录制中：先暂停，调整 Sense 朝向，再选择另一臂。整个过程保留切换标签。
**关闭浏览器不会停止遥操**。按“退出录制”或“暂停跟随”停止跟随，按“退出控制”退出网关，均不失能。
“暂停跟随”不是硬件急停。

数据目录：`~/datasets/pika_piper/<任务名称>/`，包含 `raw/`、`hdf5/`、`.trash/`、`lerobot28_时间/`。
控制台当前提供采集、切换、列表和格式转换；不含自动回零/恢复使能及离线视频回放。

服务管理：

```bash
systemctl --user status piper-collection-web.service
systemctl --user start piper-collection-web.service
journalctl --user -u piper-collection-web.service -n 80 --no-pager
```

已注册用户登录后启动的网页服务；服务启动不等于启动机械臂控制。停止网页服务时会尝试结束其录制及退出其启动的控制进程。
控制台后端为 `web_console.py`、页面为 `web_console.html`，复用 `switch.sh` / `run.sh` / `export_aloha.py` / `convert_lerobot.sh`。
复用 `.session.lock` 避免 CLI 与网页同时开录；请求令牌及同源检查阻止跨站触发操作。
网页测试覆盖旧录制 ID、并发操作、保存/丢弃、回收站、路径越界和未授权请求；部署后通过真实浏览器确认三路 640×480 画面在线、页面无脚本异常。
网页按钮调用逻辑已离线测试，真实机械臂的左右切换及连续采集通过前述命令行入口完成现场验收。

## 命令行启动与切换

都在 175 的 `~/piper_runtime/collection` 运行。不要同时启动旧 teleop、推理或回零程序。
传感器已运行时不用重复启动；否则终端 A：

```bash
cd ~/piper_runtime/collection
bash run.sh --profile profile_single_left.json sensors --execute
```

终端 B，先做只读预览（不发 CAN 运动命令）：

```bash
bash switch.sh serve
# 另一终端
bash switch.sh preview-left
bash switch.sh preview-right
bash switch.sh pause
bash switch.sh stop
```

现场准备完成后，终端 B 启动实际控制。启动时读两臂当前位置作为保持目标，包含夹爪；
与旧驱动启动闭爪不同，本入口不会主动闭爪。

```bash
bash switch.sh serve --execute
```

另一终端明确选择，不使用不可靠的双击或 toggle：

```bash
bash switch.sh left
bash switch.sh right
bash switch.sh pause
bash switch.sh status
bash switch.sh stop
```

每次选择前让 Sense 朝向与目标臂末端一致并保持静止。
切换先让两臂保持当前位置，退出旧 FK/IK/遥操进程，再从新臂真实状态和当前 Sense 位姿建立相对参考。
每轮前端使用随机唯一节点/服务名；CAN 发布者保持不变，拒收重启前时间戳的旧 IK 数据。
退出会按该轮唯一节点名回收 FK/IK/遥操子进程（包括 ROS launch 已退出的孤儿进程），逐级 SIGINT/SIGTERM/SIGKILL 并确认退出；清理失败不允许切换。
2026-10-15 修复了只等待 launch 主进程导致 IK 孤儿残留的问题；修复后只读左→右→左和退出复测通过，无残留。
选择命令可能等待数秒；只有返回 phase=active 且 error=null 才进入控制。
`pause` 明确停止跟随并发送当前位置保持目标；`stop` 另退出网关。均不发送失能/归零。
输入丢失自动暂停；CAN/电机故障停止发送并锁存错误，需要检查后重启。硬件已接受的运动不等同于立即制动。

## 交互式采集（参照 174 的逐条流程）

终端 C：

```bash
cd ~/piper_runtime/collection
bash session.sh --task '左臂拿起物体，右臂接过并放入容器' \
  --output ~/datasets/pika_piper/task_handover
```

- Enter：开始一条；等待显示 Recording started 后示教；再按 Enter 保存并检查。
- l / r：切换左/右控制，可在同一条录制中切换。
- p：暂停遥操，录制继续，记录保持目标。
- d：丢弃当前条（原始记录保留并标为未接受，不导出；不会删除旧条目）。
- q：退出采集；未保存条不导出。**退出录制不等于停止遥操**，另执行 `bash switch.sh pause` 或 `stop`。

每条先写入 `task_handover/raw/episode_.../`，质量通过并接受后导出为
`task_handover/hdf5/episode_0.hdf5` 等，拒绝覆盖。真实双臂示教首条应检查画面、左右身份及运动一致性。

## 数据与 174 的对应

174 入口：`/home/agilex/Desktop/Cobot数据可视化.desktop` → `~/cobot_magic/start_visualization.sh`
→ `collect_data/web_server.py` → `collect_interactive.py`，ROS1 Noetic / aloha 环境。
175 保留 ROS2 Humble / pika 控制环境；复用流程与文件约定，不直接运行 174 的 ROS1 硬件控制入口。

兼容 HDF5：

- observations/qpos：N×14，左六关节、左夹爪、右六关节、右夹爪；单位 rad/m。
- action：N×14，相同顺序，包含非操控臂实际发送的保持目标；不是反馈伪装为目标。
- observations/images/cam_high、cam_left_wrist、cam_right_wrist：N×480×640×3 uint8 RGB。
- timestamp：对齐相机时间；control_mode：每帧 JSON，含选中/活动侧、phase、generation。
- 原始 ROS/NPZ 数据仍保留原始顺序 left6,right6,left_gripper,right_gripper，导出显式重排。
- 原始相机 BGR，兼容 HDF5 转成 RGB。速度、力矩、底盘数据未采到的字段省略，不用零填造数据。

原始录制 10 Hz；174 默认 30 Hz。转换必须使用实际 10 Hz，不能仅改元数据冒充 30 Hz。
暂停/切换片段保留 phase 标签，训练时可自行选择过滤，原始记录不静默删帧。

## LeRobot 转换

`reference_174/` 是从 174 复制的 v3.0/v2.1 转换源码，provenance.json 记录来源及 SHA256。
174 默认 legacy28 是**由实测 qpos/FK 得出的 28 维动作**，不是 HDF5 原始目标。
175 包装入口按用户选择默认 legacy28，与 174 一致；HDF5 仍保留实际下发的 14 维目标，显式 `--action-layout raw` 可另行导出。

175 新建独立转换环境（不改控制环境）。主要版本与 174 对齐：LeRobot 0.4.4、PyTorch 2.10.0（175 用 CPU 包）、NumPy 2.2.6、SciPy 1.14.1。安装脚本使用清华 PyPI 镜像下载通用依赖，可用 `PIKA_EXPORT_PYPI_INDEX` 覆盖：

```bash
bash setup_export_env.sh --execute
export LEROBOT_PYTHON="$HOME/miniconda3/envs/pika_export/bin/python"
bash convert_lerobot.sh \
  --input_dir "$HOME/datasets/pika_piper/task_handover/hdf5" \
  --output_dir "$HOME/datasets/pika_piper/task_handover_lerobot" \
  --fps 10 --action-layout legacy28 --image-color-order rgb \
  --prompt '左臂拿起物体，右臂接过并放入容器'
```

输出目录必须独立且为空。包装入口会检查帧率/色彩属性，并把切换标签保存到
`meta/control_modes.jsonl`，这些标签不是 LeRobot 内置训练 feature。HDF5 和原始数据不会被删改或上传。
legacy28 复用 174 的 v2.1 helper 和原样保存的 `reference_174/piper_fk.py`，仅将 helper 的 SDK 导入改为该离线 FK 文件，不依赖 CAN。FK 默认启用原版 J2/J3 的 2° DH 偏移。末端 xyz 单位为毫米，姿态顺序为 qx,qy,qz,qw；动作顺序为左位姿7、右位姿7、左关节6、右关节6、左右夹爪各1。

验收自己的转换结果：

```bash
"$LEROBOT_PYTHON" verify_lerobot.py \
  "$HOME/datasets/pika_piper/task_handover/hdf5" \
  "$HOME/datasets/pika_piper/task_handover_lerobot"
```
