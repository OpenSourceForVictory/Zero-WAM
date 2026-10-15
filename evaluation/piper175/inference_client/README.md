# 174 merged 客户端迁移到 175

部署目录：`/home/piperpika/piper_runtime/inference_client`。
入口保持 `real_robot_inference_merged.py`，来源为 174 的
`/home/agilex/models/lingbot-va-piper/client/real_robot_inference_merged.py`。
`source174/` 保留导入原文，`source_manifest.json` 保存 SHA256；未修改 174 原客户端。

## 已验证

2026-10-13 在 175 真实读取相机和关节反馈，通过 SSH 转发接到 174 已有的模型服务：

`175:127.0.0.1:39056 → SSH 174 → 174:127.0.0.1:39056 → 原推理服务`

`--check` 成功，`breakfast + bread_test000` 的 `--shadow` 成功，返回动作
`(28, 2, 16)`，保存于
`inference_logs/20261013_101650_843070_shadow/prediction_0000.npy`。
该示教取自 174 启动脚本提供的测试示例，不声称它就是 174 上次执行所用的示教。
影子测试没有使能、切换机械臂控制模式或发送运动/夹爪指令。
19 项离线测试通过。运动控制、抓取效果尚未实机验证。

该预测经原版 spline 平滑后为 39 步，跳过初始 latent 后第一步为索引 19。
当时与姿态相比，左臂 J2 差约 87.47°，被当时新增的 15° 保护拦截。
`prediction_analysis.json` 保存完整对比。需现场确定/到达合适起始姿态后重新预测，
不重放旧结果。后续按用户要求恢复 174 执行参数，默认不再启用额外角度差拦截。

## 使用

在 175 执行：

```bash
bash ~/piper_runtime/can_up.sh
systemctl --user start piper-cameras.service
cd ~/piper_runtime/inference_client
bash run.sh --check
```

默认仅检查，不执行。硬件检查而不连模型可加 `--skip-server`。
SSH 隧道目前已建立；它不是开机服务。断开/重启后在另一个终端运行
`bash tunnel.sh` 并输入 174 的 SSH 密码，保持该终端运行。
如果提示端口占用，先确认现有隧道是否已可用，不要同时建立重复隧道。
两台机器人不要同时重置/使用同一有状态推理会话。

只做一次预测：

```bash
bash run_human_demo.sh bread_test000 --shadow
```

继承 174 的 `breakfast` 提示词和示教目录，支持
`bread_000/002/003/004` 与 `bread_test000/001`，也可用 `--prompt` 覆盖任务。
示教路径指向模型服务器，不是 175 的文件。这个启动脚本默认 shadow，
不会把未执行的预测当成执行历史继续滚动。

以下为现场完成起始姿态检查后才使用的运动入口（本次未运行）：

```bash
bash run_human_demo.sh bread_test000 --execute
```

默认 `--gripper-mode legacy174`，使用原版左右 0.8/0.9 系数和 ≤0.02m 闭合逻辑，
并按 175 实测固件上限（左 0.10m / 右 0.07m）限制输出。
原版 0.08m 软件上限仍保留；固件参数不是物理标定。
可用 `--gripper-mode hold` 禁止夹爪指令。
`--max_chunks 0` 表示持续执行，默认 0，与 174 启动脚本一致；仅开放 MOVE_J，未开放 CPV/MOVE_JS。

运行中请求停止：

```bash
bash runtime_config.sh stop
```

停止标志在模型等待和每步发送前检查；原版到位等待期间不轮询此标志（调用超时3秒）。用户停止、Ctrl+C或SIGTERM仅断开连接，不发电子停止/reset/disable，保持原控制状态和最后目标。
真实故障在已开始使能/运动后仍发电子停止；可能阻尼下落，不等于位置保持。
正常结束只断开，不回零、不失能。再次开始前明确执行 `bash runtime_config.sh resume`。
网络断开/模型响应最长等待 300 秒；此期间仍会检查软件停止标志。

## 适配内容和保留行为

- `site_controller.py` 通过 USB `1-2.2:1.0` 找左臂，`1-2.1:1.0` 找右臂。
  本次为左 can1、右 can0，与前一天相反；不硬编码枚举名。
- 固件实测 S-V1.7-3，使用 `default` profile。接入时再次检查固件和新鲜反馈。
- `camera_manager.py` 从本地服务 `/observation.npz` 读取无损 BGR 图像，
  复用网页采集线程，不直接占用设备，不依赖 ROS 1。模型入口只转换一次 RGB。
  high/left/right 对应 0041/002Y/0015；沿用当前采集方向（三路旋转 0°）。
  请求读取最新原帧，不受网页 JPEG 编码的 10 FPS 限制。检查图像年龄 <0.5 秒、
  三路软件时间戳差 <150ms；不是硬件同步相机。
- 保留原版 30D state 填充方式（前 12 个为左右关节弧度）、14/28D 关节动作解析、
  spline 平滑、初始 latent 跳过、观测和 KV-cache 协议。
  原版以预测动作更新 KV-cache 的行为保留，不宣称它是精确测量执行历史。
- 默认 6% 速度，与 174 一致（可选 1–6%）；发送前检查示教状态、错误、反馈新鲜度、使能反馈，
  默认取消新增的 15° 关节偏差拦截；可显式传 `--max-target-delta-deg 15` 启用。
  执行时会发送模型的绝对关节目标，不会另做起始姿态规划。
  到位判断直接继承174原版：状态到位或角度达标；超时/停滞返回False，调用处沿用原版继续。
  这些保护没有做碰撞检测，也不证明两个安装环境下策略效果相同。
- 只有显式 `--execute` 才可能使能；相机/服务检查和首个目标检查在使能前完成。
  `--shadow` 仅一个 chunk，不触发动作、夹爪、KV-cache 更新。
- 日志保存源观测、来源映射、示教路径、原始预测和结果。
  没有自动解码服务器上的预测视频，也没有修改原 174 日志目录。

## 文件

`source174/` 原文；`real_robot_inference_merged.py` 适配入口；
`site_controller.py` 硬件适配；`camera_manager.py` 无损取图；
`va_client.py` 有超时和停止检测的原协议客户端；`run_human_demo.sh` 示教入口。

## 2026-10-13 执行参数修复

恢复 174 启动配置：6% 速度、持续循环、legacy174 夹爪逻辑，默认关闭额外角度差拦截。
保留显式 --execute 入口、示教/故障/反馈检查、原版到位等待行为和本机夹爪范围。
仅部署与只读检查，不以此次修复宣称实机任务成功。174 成功那次的模型权重、
示教 ID 和起始姿态尚未核实；bread_test000 仍只是两边启动脚本支持的示例。
最新试跑隧道为 175:39056 → 工作站:49056 → 远端模型:29056；
上文经 174 的隧道是首次验证时的路径，tunnel.sh 仅适用于该旧路径可用时。

## 177 GPU 推理服务（2026-10-14）

服务地址：`ws://192.168.8.177:29056`。使用云端 ALOHA `checkpoint_step_6000` 的后训练权重，50 steps、2 chunk frames。177 上使用 RTX 8000，transformer FP16、VAE FP32。

```bash
cd ~/piper_runtime/inference_client
bash run_gpu_demo.sh bread_test000 --shadow
```

`run_gpu_demo.sh` 设置服务器地址及服务器端演示 latent 路径；默认 shadow。原 `run_human_demo.sh` 的云端默认值保留。可用 `ZERO_WAM_SERVER_HOST`、`ZERO_WAM_SERVER_PORT`、`ZERO_WAM_SERVER_DATA_ROOT` 覆盖 GPU 入口配置。

本次仅运行固定观测回放，没有机器人动作，也没有修改 `runtime_config.json` 的 `emergency_stop: true`。普通客户端启动仍遵循现有停止标志检查。

验证结果：`benchmark_logs/gpu177_validation_20261014_v2/report.json`。连续两组动作输出均为 `(28, 2, 16)`，全部数值有限；请求耗时约 15.46 / 16.28 秒，KV 更新约 1.43 秒。健康检查返回 HTTP 200，动作契约 `aloha_absolute_28_continuous_v2` 已绑定。这是固定观测及合成历史的通信/推理验证，不代表实机任务成功率或实时控制性能。

177 服务管理：`systemctl --user start|stop|status zero-wam`；日志 `/home/huawei/Zero-WAM/outputs/server_gpu.log`。也可在停止托管服务后，进入 `/home/huawei/Zero-WAM` 执行 `bash start_infer.sh` 前台启动。

### 25 steps 与诊断记录（2026-10-14 更新）

177 默认已改为视频/动作各 25 steps，仍为 2 chunk frames，模型及数值精度不变。
服务端 `~/Zero-WAM/outputs/diagnostics/*.jsonl` 记录请求类型、步骤数、分段耗时、KV token 数及动作统计，并通过响应的 `diagnostics` 字段返回。`ZERO_WAM_DIAGNOSTICS=0` 可在启动前关闭计时。

175 每次运行目录新增：
- `rpc_diagnostics.jsonl`：打包、请求等待、解包耗时及对应服务端诊断。
- `action_diagnostics.jsonl`：首块跳过填充后的关节变化、夹爪范围、初始目标偏差等。
- `execution_feedback.jsonl`：实机关键帧目标/反馈角度、最大误差及现有到位等待返回值。
- `chunk_timings.jsonl`：逐块推理、执行、KV 时间。
- `result.json` 新增 `sent_steps`，中断时也保留实际发送计数。

诊断不改变现有运动控制/等待/停止逻辑。关键帧误差和输出有限性不代表任务成功；训练分位数越界仅是分布提示。
本次真实测试 `inference_logs/20261014_150935_001790_execute` 连续完成 5 块、144 步，平均推理 8.73 秒。首块关键帧反馈误差 31–50 度且等待返回 false，后续采样误差小于 3 度；应优先排查首块起始姿态与到位等待。测试结束软件停止恢复，无自动归零。


### 故障退出修复（2026-10-14）

当前推理客户端遇软件异常时停止发送新动作、置位软件停止并断开连接，不再自动下发电子停止/reset/disable。不会自动恢复。该修改消除了已知的软件异常触发电子停止路径，但不构成经过验证的位置保持；已接收的目标仍可能继续执行。机械臂保持停止，详见 OPERATIONS.md 的事件后修复记录。
